import argparse
import asyncio
import os
from pathlib import Path
from typing import Any

from livekit.agents import AgentSession, inference
from livekit.agents._exceptions import APIConnectionError, APIStatusError

from agent import DefaultAgent
from product_eval import build_eval_cases, load_products, save_cases_json
from product_scoring import dump_json, score_answer, score_summary


def _repo_root() -> Path:
    # src/run_product_eval.py -> agent-starter-python -> repo root
    return Path(__file__).resolve().parents[2]


def _extract_assistant_text(events: list[Any]) -> str:
    # Prefer the last assistant message event.
    for ev in reversed(events):
        item = getattr(ev, "item", None)
        if item is None:
            continue
        role = getattr(item, "role", None)
        if role != "assistant":
            continue
        content = getattr(item, "content", None)
        if isinstance(content, list):
            return "\n".join(str(x) for x in content if x is not None).strip()
        if isinstance(content, str):
            return content.strip()
    return ""


def _parse_price(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _derive_expected_from_tool(
    case_kind: str, expected: dict[str, Any], tool_hits: list[dict[str, Any]]
) -> dict[str, Any]:
    """
    For certain question types, the agent only has access to the rows returned by the
    retrieval tool (top-5). To avoid unfair "omniscient CSV" scoring, we derive the
    expected answer from the retrieved hits.
    """
    if case_kind in {"cheapest_in_category", "most_expensive_in_category"}:
        priced: list[tuple[float, dict[str, Any]]] = []
        for r in tool_hits:
            p = _parse_price(r.get("price"))
            if p is not None:
                priced.append((p, r))
        if not priced:
            return {**expected, "derived_from": "tool_hits", "price": None}

        if case_kind == "cheapest_in_category":
            p, row = min(priced, key=lambda t: t[0])
        else:
            p, row = max(priced, key=lambda t: t[0])
        return {
            **expected,
            "derived_from": "tool_hits",
            "row_id": row.get("id"),
            "description": row.get("description"),
            "price": p,
        }

    return expected


def _write_report_md(out_dir: Path, summary: dict[str, Any], results: list[dict[str, Any]]) -> None:
    lines: list[str] = []
    lines.append("# Product QA evaluation report\n")
    lines.append(f"- n: **{summary.get('n')}**\n")
    lines.append(f"- overall_accuracy: **{summary.get('overall_accuracy'):.3f}**\n")
    lines.append("\n## Accuracy by question type\n")
    for kind, acc in (summary.get("by_kind_accuracy") or {}).items():
        lines.append(f"- **{kind}**: {acc:.3f}\n")

    # Show a few failures for qualitative write-up
    failures = [r for r in results if not r["score"]["ok"]]
    lines.append("\n## Sample failures (first 10)\n")
    for r in failures[:10]:
        lines.append(f"### {r['id']} ({r['kind']})\n")
        lines.append(f"**Q:** {r['question']}\n\n")
        lines.append(f"**Expected:** `{r['expected']}`\n\n")
        ans = (r.get("answer") or "").replace("\n", " ").strip()
        if len(ans) > 400:
            ans = ans[:400] + "…"
        lines.append(f"**Answer:** {ans}\n\n")

    (out_dir / "REPORT.md").write_text("".join(lines), encoding="utf-8")


async def _run(args: argparse.Namespace) -> int:
    csv_path = Path(args.csv_path)
    if not csv_path.is_absolute():
        csv_path = (_repo_root() / csv_path).resolve()

    rows = load_products(csv_path)
    cases = build_eval_cases(rows, n=args.n, seed=args.seed)

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = (_repo_root() / out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    save_cases_json(cases, out_dir / "eval_cases.json")

    llm_model = args.llm_model or os.environ.get("EVAL_LLM_MODEL") or "openai/gpt-4o-mini"

    results: list[dict[str, Any]] = []
    agent = DefaultAgent(metadata="{}", fallback_conversation_id="eval")
    async with (
        inference.LLM(model=llm_model) as llm,
        AgentSession(llm=llm) as session,
    ):
        await session.start(agent)

        # Run cases sequentially for reproducibility
        for case in cases:
            attempt = 0
            while True:
                attempt += 1
                try:
                    rr = await session.run(user_input=case.question)
                    answer_text = _extract_assistant_text(rr.events)
                    # The DefaultAgent uses a real remote tool (Supabase edge function).
                    # In this eval runner we don't currently capture tool calls/hits, so
                    # we score against the original expected values.
                    tool_calls: list[dict[str, Any]] = []
                    derived_expected = case.expected
                    score = score_answer(case.kind, derived_expected, answer_text)
                    results.append(
                        {
                            "id": case.id,
                            "kind": case.kind,
                            "question": case.question,
                            "expected": derived_expected,
                            "answer": answer_text,
                            "tool": {"calls": tool_calls},
                            "score": {"ok": score.ok, "details": score.details},
                        }
                    )
                    break
                except (APIStatusError, APIConnectionError) as e:
                    # Common during eval runs: token/min rate limiting (429) from the gateway.
                    # Back off and retry the same case.
                    if attempt > args.max_retries:
                        results.append(
                            {
                                "id": case.id,
                                "kind": case.kind,
                                "question": case.question,
                                "expected": case.expected,
                                "answer": "",
                                "tool": {"calls": []},
                                "score": {
                                    "ok": False,
                                    "details": {
                                        "error": type(e).__name__,
                                        "message": str(e),
                                        "attempts": attempt,
                                    },
                                },
                            }
                        )
                        break

                    wait_s = min(
                        args.max_backoff_s,
                        args.base_backoff_s * (2 ** (attempt - 1)),
                    )
                    await asyncio.sleep(wait_s)
            if args.sleep_ms > 0:
                await asyncio.sleep(args.sleep_ms / 1000.0)

    summary = score_summary(results)
    dump_json(str(out_dir / "eval_results.json"), results)
    dump_json(str(out_dir / "eval_summary.json"), summary)
    _write_report_md(out_dir, summary, results)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Run 100-question product QA evaluation")
    p.add_argument(
        "--csv-path",
        default="product_data - products_rows (1).csv",
        help="Path to product CSV (relative to repo root by default)",
    )
    p.add_argument("--n", type=int, default=100, help="Number of eval questions")
    p.add_argument("--seed", type=int, default=7, help="RNG seed for question generation")
    p.add_argument(
        "--out-dir",
        default="agent-starter-python/eval_artifacts",
        help="Output directory (relative to repo root by default)",
    )
    p.add_argument(
        "--llm-model",
        default=None,
        help="Override LLM model (or set EVAL_LLM_MODEL)",
    )
    p.add_argument(
        "--sleep-ms",
        type=int,
        default=750,
        help="Sleep between questions to reduce rate limiting",
    )
    p.add_argument(
        "--max-retries",
        type=int,
        default=6,
        help="Max retries per question on transient LLM errors",
    )
    p.add_argument(
        "--base-backoff-s",
        type=float,
        default=2.0,
        help="Base backoff (seconds) for retries",
    )
    p.add_argument(
        "--max-backoff-s",
        type=float,
        default=90.0,
        help="Maximum backoff (seconds) for retries",
    )
    args = p.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())

