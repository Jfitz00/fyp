import json
import re
from dataclasses import dataclass
from typing import Any, Literal


def _extract_numbers(text: str) -> list[float]:
    # Accept "106.01", "106,01", "€106.01"
    cleaned = text.replace(",", ".")
    nums = re.findall(r"(?<!\w)(\d+(?:\.\d+)?)(?!\w)", cleaned)
    out: list[float] = []
    for n in nums:
        try:
            out.append(float(n))
        except Exception:
            continue
    return out


def _mentions_any(text: str, needles: list[str]) -> bool:
    t = text.lower()
    return any((n or "").lower() in t for n in needles if n)


@dataclass(frozen=True)
class ScoreResult:
    ok: bool
    kind: str
    details: dict[str, Any]


def score_answer(case_kind: str, expected: dict[str, Any], answer_text: str) -> ScoreResult:
    """
    Deterministic scorer for the most common question types.
    For ambiguous cases, we score "OK" if the agent asks a question or lists options.
    """
    a = (answer_text or "").strip()
    a_low = a.lower()

    if case_kind == "price_lookup":
        exp = expected.get("price")
        if exp is None:
            return ScoreResult(ok=False, kind=case_kind, details={"reason": "no_expected_price"})
        nums = _extract_numbers(a)
        ok = any(abs(n - float(exp)) <= 0.02 for n in nums)  # tolerate rounding
        return ScoreResult(ok=ok, kind=case_kind, details={"expected_price": exp, "found_numbers": nums[:10]})

    if case_kind == "location_lookup":
        loc = expected.get("location")
        ok = bool(loc) and (str(loc).lower() in a_low)
        return ScoreResult(ok=ok, kind=case_kind, details={"expected_location": loc})

    if case_kind == "barcode_lookup":
        barcode = expected.get("barcode")
        ok = bool(barcode) and (str(barcode) in a)
        return ScoreResult(ok=ok, kind=case_kind, details={"expected_barcode": barcode})

    if case_kind == "category_count":
        # With a top-k retrieval tool (k=5), the agent typically cannot compute the
        # global category count. Score "OK" if it *doesn't hallucinate* and instead
        # explains the limitation / asks to refine.
        limitation = any(
            p in a_low
            for p in [
                "i only have",
                "i can only see",
                "only the returned",
                "only based on",
                "top 5",
                "five results",
                "5 results",
                "i found 5",
                "i found five",
                "please clarify",
                "can you be more specific",
            ]
        )
        refused = "sorry i don’t know" in a_low or "i don't know" in a_low
        # Accept either a limitation explanation or a refusal.
        ok = bool(limitation or refused)
        return ScoreResult(
            ok=ok,
            kind=case_kind,
            details={"heuristic": "limitation_or_refusal_for_global_count"},
        )

    if case_kind == "category_under_price":
        # Hard to deterministically score list membership from natural language.
        # We mark OK if the agent gives a list-like answer and doesn't refuse.
        refused = "sorry i don’t know" in a_low or "sorry i don't know" in a_low
        listish = ("\n" in a) or ("- " in a) or ("•" in a) or ("," in a)
        ok = (not refused) and listish
        return ScoreResult(ok=ok, kind=case_kind, details={"heuristic": "listish_and_not_refusal"})

    if case_kind in {"cheapest_in_category", "most_expensive_in_category"}:
        exp_price = expected.get("price")
        nums = _extract_numbers(a)
        ok = False
        if exp_price is not None:
            ok = any(abs(n - float(exp_price)) <= 0.02 for n in nums)
        # Also accept if it clearly mentions the expected description substring
        desc = str(expected.get("description") or "")
        ok = ok or _mentions_any(a, [desc[:18] if len(desc) >= 18 else desc])
        return ScoreResult(ok=ok, kind=case_kind, details={"expected_price": exp_price, "found_numbers": nums[:10]})

    if case_kind == "ambiguous_lookup":
        # Expect clarification or multiple candidates
        asks = "?" in a or any(w in a_low for w in ["which", "do you mean", "can you clarify", "could you clarify", "what size", "what category"])
        multi = ("\n" in a and any(ch.isdigit() for ch in a)) or ("options" in a_low) or ("i found" in a_low and "items" in a_low)
        return ScoreResult(ok=bool(asks or multi), kind=case_kind, details={"heuristic": "asks_or_multi"})

    # Fallback: mark as unknown scoring
    return ScoreResult(ok=False, kind=case_kind, details={"reason": "unscored_kind"})


def score_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_kind: dict[str, list[bool]] = {}
    for r in results:
        by_kind.setdefault(r["kind"], []).append(bool(r["score"]["ok"]))

    def rate(xs: list[bool]) -> float:
        return (sum(1 for x in xs if x) / len(xs)) if xs else 0.0

    return {
        "overall_accuracy": rate([bool(r["score"]["ok"]) for r in results]),
        "by_kind_accuracy": {k: rate(v) for k, v in sorted(by_kind.items())},
        "n": len(results),
    }


def dump_json(path: str, obj: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)

