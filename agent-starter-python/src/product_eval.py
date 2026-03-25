import csv
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal


def _normalize_text(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[*]+", " ", s)
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


_MEASURE_TOKEN = re.compile(
    r"""^(
        \d+(\.\d+)?(cm|mm|m|l|ml|g|gr|kg|pcs|pc|x)?
        |d\d+(\.\d+)?
        |\d+x\d+(\.\d+)?(x\d+(\.\d+)?)?
    )$""",
    re.IGNORECASE | re.VERBOSE,
)


def roughen_description(desc: str) -> str:
    """
    Produces a user-like "rough" query from a messy product description by removing
    measurements/pack counts and obvious noise.
    """
    norm = _normalize_text(desc)
    tokens = [t for t in norm.split() if not _MEASURE_TOKEN.match(t)]
    # Drop extremely generic leading tokens often in this dataset
    while tokens and tokens[0] in {"deco", "decoration", "ceramics", "baskets"}:
        # Keep one generic word at most; users often still say "ceramics pot"
        break
    # Keep first N tokens to simulate short user queries
    return " ".join(tokens[:7]).strip() or norm[:40]


def load_products(csv_path: str | Path) -> list[dict[str, str]]:
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return [row for row in reader]


def _parse_price(value: str) -> float | None:
    try:
        v = float(value)
        return v
    except Exception:
        return None


@dataclass(frozen=True)
class EvalCase:
    id: str
    kind: Literal[
        "price_lookup",
        "location_lookup",
        "barcode_lookup",
        "category_count",
        "category_under_price",
        "cheapest_in_category",
        "most_expensive_in_category",
        "ambiguous_lookup",
    ]
    question: str
    expected: dict[str, Any]


def _sample_with_price(rows: list[dict[str, str]], rng: random.Random) -> dict[str, str]:
    for _ in range(2000):
        row = rng.choice(rows)
        if _parse_price(row.get("price", "")) is not None:
            return row
    return rng.choice(rows)


def build_eval_cases(
    rows: list[dict[str, str]],
    n: int = 100,
    seed: int = 7,
) -> list[EvalCase]:
    """
    Creates 100-ish realistic product questions, biased toward rough descriptions.
    All expectations are computed directly from the CSV rows.
    """
    rng = random.Random(seed)
    by_category: dict[str, list[dict[str, str]]] = {}
    for r in rows:
        by_category.setdefault(r.get("category", "").strip(), []).append(r)

    categories = [c for c in by_category.keys() if c]
    if not categories:
        raise ValueError("No categories found in CSV")

    cases: list[EvalCase] = []

    def add(case: EvalCase) -> None:
        cases.append(case)

    # 1) Lookups: price/location/barcode using rough descriptions
    for i in range(40):
        row = _sample_with_price(rows, rng)
        q = roughen_description(row.get("description", ""))
        add(
            EvalCase(
                id=f"price_lookup_{i}",
                kind="price_lookup",
                question=f"How much is {q}?",
                expected={
                    "row_id": row.get("id"),
                    "description": row.get("description"),
                    "price": _parse_price(row.get("price", "")),
                    "currency": "EUR",
                },
            )
        )

    for i in range(12):
        row = rng.choice(rows)
        q = roughen_description(row.get("description", ""))
        add(
            EvalCase(
                id=f"location_lookup_{i}",
                kind="location_lookup",
                question=f"Where can I find {q} in the warehouse (location)?",
                expected={
                    "row_id": row.get("id"),
                    "description": row.get("description"),
                    "location": row.get("location"),
                },
            )
        )

    for i in range(8):
        row = rng.choice(rows)
        q = roughen_description(row.get("description", ""))
        add(
            EvalCase(
                id=f"barcode_lookup_{i}",
                kind="barcode_lookup",
                question=f"Do you have the barcode for {q}?",
                expected={
                    "row_id": row.get("id"),
                    "description": row.get("description"),
                    "barcode": row.get("barcode"),
                },
            )
        )

    # 2) Aggregations / filters
    for i in range(12):
        cat = rng.choice(categories)
        add(
            EvalCase(
                id=f"category_count_{i}",
                kind="category_count",
                question=f"How many products are in the category {cat}?",
                expected={"category": cat, "count": len(by_category.get(cat, []))},
            )
        )

    for i in range(12):
        cat = rng.choice(categories)
        threshold = rng.choice([5, 10, 15, 20, 30])
        in_cat = by_category.get(cat, [])
        filtered = []
        for r in in_cat:
            p = _parse_price(r.get("price", ""))
            if p is not None and p <= threshold:
                filtered.append(r)
        # Keep expectations as IDs to avoid brittle string matching in evaluation
        add(
            EvalCase(
                id=f"category_under_price_{i}",
                kind="category_under_price",
                question=f"List up to 5 items in {cat} that cost {threshold} euros or less.",
                expected={
                    "category": cat,
                    "threshold": threshold,
                    "matching_ids": [r.get("id") for r in filtered],
                },
            )
        )

    for i in range(8):
        cat = rng.choice(categories)
        in_cat = by_category.get(cat, [])
        priced: list[tuple[float, dict[str, str]]] = []
        for r in in_cat:
            p = _parse_price(r.get("price", ""))
            if p is not None:
                priced.append((p, r))
        if not priced:
            continue
        pmin, rmin = min(priced, key=lambda t: t[0])
        add(
            EvalCase(
                id=f"cheapest_in_category_{i}",
                kind="cheapest_in_category",
                question=f"What is the cheapest product in {cat} and how much is it?",
                expected={
                    "category": cat,
                    "row_id": rmin.get("id"),
                    "description": rmin.get("description"),
                    "price": pmin,
                },
            )
        )

    for i in range(8):
        cat = rng.choice(categories)
        in_cat = by_category.get(cat, [])
        priced = []
        for r in in_cat:
            p = _parse_price(r.get("price", ""))
            if p is not None:
                priced.append((p, r))
        if not priced:
            continue
        pmax, rmax = max(priced, key=lambda t: t[0])
        add(
            EvalCase(
                id=f"most_expensive_in_category_{i}",
                kind="most_expensive_in_category",
                question=f"What is the most expensive item in {cat}?",
                expected={
                    "category": cat,
                    "row_id": rmax.get("id"),
                    "description": rmax.get("description"),
                    "price": pmax,
                },
            )
        )

    # 3) Ambiguity: intentionally use very short queries that will likely match many
    # We model "correct" behavior as asking a clarification OR listing multiple candidates.
    for i in range(10):
        row = rng.choice(rows)
        desc = _normalize_text(row.get("description", ""))
        tokens = [t for t in desc.split() if t and not _MEASURE_TOKEN.match(t)]
        short = " ".join(tokens[:2]) if len(tokens) >= 2 else (tokens[0] if tokens else "sisal")
        add(
            EvalCase(
                id=f"ambiguous_lookup_{i}",
                kind="ambiguous_lookup",
                question=f"How much is the {short} one?",
                expected={"query": short, "acceptable": "clarify_or_multi"},
            )
        )

    # Ensure exactly n (stable order) by trimming or topping up with more price lookups
    if len(cases) < n:
        for i in range(len(cases), n):
            row = _sample_with_price(rows, rng)
            q = roughen_description(row.get("description", ""))
            add(
                EvalCase(
                    id=f"price_lookup_extra_{i}",
                    kind="price_lookup",
                    question=f"Price for {q}?",
                    expected={
                        "row_id": row.get("id"),
                        "description": row.get("description"),
                        "price": _parse_price(row.get("price", "")),
                        "currency": "EUR",
                    },
                )
            )
    return cases[:n]


def save_cases_json(cases: Iterable[EvalCase], out_path: str | Path) -> None:
    payload = [
        {
            "id": c.id,
            "kind": c.kind,
            "question": c.question,
            "expected": c.expected,
        }
        for c in cases
    ]
    Path(out_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")

