# impl: FR-002-03
"""
BM25 FTS5 retriever for AI Tutor Service.

Performs full-text search over MaterialSegment FTS5 index with
course/unit isolation, normalized rank scores, and configurable thresholds.
"""

import re
from typing import Any

from django.db import connection


class MaterialRetriever:
    """
    BM25 retriever using SQLite FTS5.

    Configuration (from tutor_config.yaml):
    - retrieval.top_k: max results to return (default 5)
    - retrieval.min_rank_score: minimum normalized score threshold (default 0.10)

    Search is strictly isolated by course_id + unit_usage_key.
    """

    def __init__(self, config: dict[str, Any]):
        """
        Initialize retriever with config.

        Args:
            config: Full tutor config dict from load_tutor_config()
        """
        retrieval_config = config.get("retrieval", {})
        self.top_k = retrieval_config.get("top_k", 5)
        self.min_rank_score = retrieval_config.get("min_rank_score", 0.10)

        # Validate config values
        if not isinstance(self.top_k, int) or self.top_k <= 0:
            raise ValueError("retrieval.top_k must be positive integer")
        if not isinstance(self.min_rank_score, (int, float)) or not (0.0 < self.min_rank_score <= 1.0):
            raise ValueError("retrieval.min_rank_score must be in (0.0, 1.0]")

    def _sanitize_query(self, query: str) -> str:
        """
        Sanitize query for FTS5 MATCH.

        FTS5 MATCH syntax: terms, phrases ("..."), prefix (*), NEAR.
        We escape special characters and use simple term matching.
        """
        if not query or not query.strip():
            return ""

        # Escape FTS5 special characters: " * - + ( ) { } ^ @
        # We'll use simple term matching without operators
        escaped = re.sub(r'["*\-+(){}@^]', ' ', query)
        # Split into terms, filter empty
        terms = [t for t in escaped.split() if t]
        if not terms:
            return ""
        # Join with OR for broad matching
        return " OR ".join(terms)

    def _normalize_bm25_rank(self, raw_rank: float) -> float:
        """
        Normalize BM25 rank to [0, 1] range.

        SQLite FTS5 bm25() returns lower scores for better matches.
        We invert and normalize using a sigmoid-like transformation.
        """
        if raw_rank <= 0:
            return 1.0
        # Use 1 / (1 + rank) to map [0, inf) -> (0, 1]
        # This gives: rank=0 -> 1.0, rank=1 -> 0.5, rank=4 -> 0.2, rank=9 -> 0.1
        return 1.0 / (1.0 + raw_rank)

    def search(
        self,
        query: str,
        course_id: str,
        unit_usage_key: str,
    ) -> list[dict[str, Any]]:
        """
        Search for segments matching query in the given course/unit.

        Args:
            query: Search query string
            course_id: Course identifier
            unit_usage_key: Unit usage key

        Returns:
            List of result dicts with: segment_id, kind, source_ref, excerpt, rank_score
            Sorted by rank_score descending, limited to top_k, filtered by min_rank_score.
        """
        # Empty/whitespace query returns empty
        if not query or not query.strip():
            return []

        sanitized_query = self._sanitize_query(query)
        if not sanitized_query:
            return []

        # SQL query: join FTS5 with UnitMaterial to filter by course/unit and READY status
        # FTS5 bm25() returns score (lower = better match)
        sql = """
            SELECT
                fts.segment_id,
                fts.kind,
                fts.source_ref,
                fts.text,
                fts.section_title,
                bm25(material_segment_fts) as raw_rank
            FROM material_segment_fts fts
            JOIN materials_unitmaterial mat ON fts.material_id = mat.id
            WHERE mat.course_id = ?
              AND mat.unit_usage_key = ?
              AND mat.status = 'READY'
              AND material_segment_fts MATCH ?
            ORDER BY raw_rank ASC
            LIMIT ?
        """

        results = []
        with connection.cursor() as cursor:
            cursor.execute(sql, [course_id, unit_usage_key, sanitized_query, self.top_k * 2])
            rows = cursor.fetchall()

        for row in rows:
            segment_id, kind, source_ref, text, section_title, raw_rank = row

            # Normalize rank score to [0, 1]
            rank_score = self._normalize_bm25_rank(raw_rank)

            # Filter by min_rank_score
            if rank_score < self.min_rank_score:
                continue

            # Build excerpt: use text, possibly truncated
            excerpt = text[:500] if text else ""
            if len(text) > 500:
                excerpt = text[:497] + "..."

            results.append({
                "segment_id": segment_id,
                "kind": kind,
                "source_ref": source_ref,
                "excerpt": excerpt,
                "rank_score": rank_score,
            })

            if len(results) >= self.top_k:
                break

        # Results are already sorted by raw_rank ASC (best first), which means
        # rank_score DESC (highest first) after normalization
        return results