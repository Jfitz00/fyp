import json
import re
from dataclasses import dataclass
from typing import Any


def _norm(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[*]+", " ", s)
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _tokenize(s: str) -> set[str]:
    return {t for t in _norm(s).split() if len(t) >= 2}


@dataclass(frozen=True)
class SearchHit:
    score: float
    row: dict[str, str]


def hybrid_like_search(rows: list[dict[str, str]], query: str, k: int = 5) -> list[dict[str, Any]]:
    """
    Lightweight "hybrid-ish" retrieval (token overlap + fuzzy-ish heuristics).
    This is purposely dependency-free so you can evaluate locally without needing
    Supabase keys or embedding models.
    """
    q_tokens = _tokenize(query)
    q_norm = _norm(query)

    hits: list[SearchHit] = []
    for r in rows:
        desc = r.get("description", "")
        cat = r.get("category", "")
        loc = r.get("location", "")
        hay = " ".join([desc, cat, loc])

        h_tokens = _tokenize(hay)
        overlap = len(q_tokens & h_tokens)
        if q_tokens:
            jaccard = overlap / max(1, len(q_tokens | h_tokens))
        else:
            jaccard = 0.0

        h_norm = _norm(desc)
        # crude substring bonus for very short queries
        substr_bonus = 1.0 if q_norm and q_norm in h_norm else 0.0

        score = (2.5 * overlap) + (4.0 * jaccard) + (1.5 * substr_bonus)
        if score > 0:
            hits.append(SearchHit(score=score, row=r))

    hits.sort(key=lambda h: h.score, reverse=True)
    top = hits[:k]

    # Return rows as the webhook would: JSON objects of the product columns
    return [h.row for h in top]


def format_webhook_response(rows: list[dict[str, str]]) -> str:
    return json.dumps(rows, ensure_ascii=False)

