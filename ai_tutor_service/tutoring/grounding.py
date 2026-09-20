# impl: FR-002-03
"""
Grounding / source verifier for AI Tutor Service.

build_sources(retrieved_segments) -> list[dict]

Each returned dict has exactly keys {segment_id, kind, source_ref, excerpt}
and contains only data copied from the corresponding retrieved_segment.
The function does not consult LLM output; it is called by the pipeline after
retrieval to form the 'sources' field of a shown response.

Fail-safe: empty input or a segment missing any required field is ignored,
so the caller can never emit a shown response with empty sources.
"""

from typing import Any

REQUIRED_KEYS = ("segment_id", "kind", "source_ref", "excerpt")


def build_sources(retrieved_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Build a grounded source list from retrieved segments only.

    Args:
        retrieved_segments: List of segment dicts returned by
            MaterialRetriever.search(). Each should contain segment_id,
            kind, source_ref, excerpt (plus optionally rank_score, etc.).

    Returns:
        List of dicts with exactly {segment_id, kind, source_ref, excerpt},
        copied from the input segments. Segments missing any required field
        are ignored (fail-safe). Empty input yields [].
    """
    if not retrieved_segments:
        return []

    sources: list[dict[str, Any]] = []
    for seg in retrieved_segments:
        if not isinstance(seg, dict):
            continue
        if not all(k in seg and seg[k] for k in REQUIRED_KEYS):
            continue
        sources.append({
            "segment_id": seg["segment_id"],
            "kind": seg["kind"],
            "source_ref": seg["source_ref"],
            "excerpt": seg["excerpt"],
        })
    return sources
