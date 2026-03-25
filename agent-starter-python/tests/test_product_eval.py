from pathlib import Path

from product_eval import build_eval_cases, load_products


def test_builds_100_cases() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    csv_path = repo_root / "product_data - products_rows (1).csv"
    rows = load_products(csv_path)
    cases = build_eval_cases(rows, n=100, seed=7)

    assert len(cases) == 100
    assert len({c.id for c in cases}) == 100
    assert all(c.question.strip() for c in cases)

