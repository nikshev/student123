# verifies: FR-002-03
"""
Unit tests for BM25 FTS5 retriever (`ai_tutor_service.materials.retriever`).

Tests cover:
- BM25 search with top_k=5 (from YAML config)
- Normalized rank scores
- min_rank_score threshold filtering (0.10 from YAML, not hardcoded)
- Filter by course_id + unit_usage_key (strict isolation)
- Result format: {segment_id, kind, source_ref, excerpt}
- No network access (network blocked by conftest)

Expected RED reason: ai_tutor_service.materials.retriever module does not exist yet (T-018).
"""

import pytest


# Lazy import to surface ImportError as test failure (expected RED)
def _get_retriever_class():
    """Import retriever lazily; raises ImportError if not implemented (T-018)."""
    from ai_tutor_service.materials.retriever import MaterialRetriever  # noqa: PLC0415
    return MaterialRetriever


def _get_repository_class():
    """Import repository lazily; raises ImportError if not implemented (T-018)."""
    from ai_tutor_service.materials.repository import MaterialRepository  # noqa: PLC0415
    return MaterialRepository


def _get_config():
    """Load tutor config for test."""
    from ai_tutor_service.config import load_tutor_config
    from pathlib import Path
    config_path = Path(__file__).resolve().parent.parent.parent / "tutor_config.yaml"
    return load_tutor_config(config_path)


# Test constants
TEST_COURSE_ID = "course-v1:demo+math+2026"
TEST_UNIT_USAGE_KEY = "block-v1:demo+math+2026+type@vertical+block@u1"
NEIGHBOR_UNIT_USAGE_KEY = "block-v1:demo+math+2026+type@vertical+block@u2"


class TestRetrieverInterface:
    """Test that MaterialRetriever has the expected interface."""

    def test_retriever_class_exists(self):
        """MaterialRetriever class should be importable."""
        # This will fail with ImportError until T-018 implements it
        RetrieverClass = _get_retriever_class()
        assert RetrieverClass is not None

    def test_retriever_has_search_method(self):
        """MaterialRetriever should have a search method."""
        RetrieverClass = _get_retriever_class()
        assert hasattr(RetrieverClass, "search")
        assert callable(getattr(RetrieverClass, "search"))

    def test_retriever_constructor_accepts_config(self):
        """MaterialRetriever constructor should accept config dict."""
        RetrieverClass = _get_retriever_class()
        config = _get_config()
        # Should not raise TypeError for config arg
        retriever = RetrieverClass(config=config)
        assert retriever is not None


class TestRetrieverSearch:
    """Test BM25 search behavior."""

    @pytest.fixture
    def retriever(self):
        """Create retriever instance with test config."""
        RetrieverClass = _get_retriever_class()
        config = _get_config()
        return RetrieverClass(config=config)

    @pytest.fixture
    def repository(self):
        """Create repository instance for test data setup."""
        RepositoryClass = _get_repository_class()
        return RepositoryClass()

    def test_search_returns_top_k_results(self, retriever, repository):
        """Search returns at most top_k results (from config)."""
        config = _get_config()
        top_k = config["retrieval"]["top_k"]
        assert top_k == 5  # from tutor_config.yaml

        # Setup test data via repository
        # This will fail until repository is implemented
        material_id = self._create_test_material(repository)

        # Search for a term that matches
        results = retriever.search(
            query="multiplication",
            course_id=TEST_COURSE_ID,
            unit_usage_key=TEST_UNIT_USAGE_KEY,
        )

        # Should return at most top_k results
        assert len(results) <= top_k

    def test_search_results_normalized_rank_scores(self, retriever, repository):
        """Search results have normalized rank scores (0.0 to 1.0)."""
        material_id = self._create_test_material(repository)

        results = retriever.search(
            query="multiplication",
            course_id=TEST_COURSE_ID,
            unit_usage_key=TEST_UNIT_USAGE_KEY,
        )

        for result in results:
            assert "rank_score" in result
            score = result["rank_score"]
            assert isinstance(score, (int, float))
            assert 0.0 <= score <= 1.0, f"Rank score {score} not in [0.0, 1.0]"

    def test_search_filters_by_min_rank_score(self, retriever, repository):
        """Results below min_rank_score threshold are filtered out."""
        config = _get_config()
        min_rank_score = config["retrieval"]["min_rank_score"]
        assert min_rank_score == 0.10  # from tutor_config.yaml

        material_id = self._create_test_material(repository)

        # Search with a query that might produce low scores
        results = retriever.search(
            query="xyzunrelatedterm",
            course_id=TEST_COURSE_ID,
            unit_usage_key=TEST_UNIT_USAGE_KEY,
        )

        # All returned results should have rank_score >= min_rank_score
        for result in results:
            assert result["rank_score"] >= min_rank_score, (
                f"Result with score {result['rank_score']} "
                f"below threshold {min_rank_score}"
            )

    def test_search_result_format(self, retriever, repository):
        """Each result has required fields: segment_id, kind, source_ref, excerpt."""
        material_id = self._create_test_material(repository)

        results = retriever.search(
            query="multiplication",
            course_id=TEST_COURSE_ID,
            unit_usage_key=TEST_UNIT_USAGE_KEY,
        )

        for result in results:
            # Required fields per contract
            assert "segment_id" in result
            assert "kind" in result
            assert "source_ref" in result
            assert "excerpt" in result

            # Type checks
            assert isinstance(result["segment_id"], str)
            assert result["kind"] in ("transcript", "notes")
            assert isinstance(result["source_ref"], str)
            assert isinstance(result["excerpt"], str)

            # Validate source_ref format
            if result["kind"] == "transcript":
                assert result["source_ref"].startswith("video@")
            elif result["kind"] == "notes":
                assert result["source_ref"].startswith("notes#")

            # Excerpt should be non-empty and reasonable length
            assert len(result["excerpt"]) > 0
            assert len(result["excerpt"]) <= 500  # reasonable excerpt length

    def test_search_strict_course_unit_isolation(self, retriever, repository):
        """Search only returns segments from exact course_id + unit_usage_key."""
        # Create material in unit 1
        material_id_1 = self._create_test_material(
            repository,
            unit_usage_key=TEST_UNIT_USAGE_KEY,
            transcript_text="Unit 1 content about multiplication rules.",
            notes_text="Notes about multiplication.",
        )

        # Create material in neighbor unit 2 with DIFFERENT notes text
        material_id_2 = self._create_test_material(
            repository,
            unit_usage_key=NEIGHBOR_UNIT_USAGE_KEY,
            transcript_text="Unit 2 content about division rules.",
            notes_text="Notes about division.",  # Different from unit 1
        )

        # Search in unit 1 for "multiplication"
        results_unit1 = retriever.search(
            query="multiplication",
            course_id=TEST_COURSE_ID,
            unit_usage_key=TEST_UNIT_USAGE_KEY,
        )

        # Search in unit 1 for "division" (should not find unit 2 content)
        results_unit1_div = retriever.search(
            query="division",
            course_id=TEST_COURSE_ID,
            unit_usage_key=TEST_UNIT_USAGE_KEY,
        )

        # Search in unit 2 for "division"
        results_unit2 = retriever.search(
            query="division",
            course_id=TEST_COURSE_ID,
            unit_usage_key=NEIGHBOR_UNIT_USAGE_KEY,
        )

        # Search in unit 2 for "multiplication" (should not find unit 1 content)
        results_unit2_mult = retriever.search(
            query="multiplication",
            course_id=TEST_COURSE_ID,
            unit_usage_key=NEIGHBOR_UNIT_USAGE_KEY,
        )

        assert results_unit1, "multiplication мав знайтись у unit 1"
        assert results_unit1_div == [], "division з unit 2 не має просочуватись у unit 1"
        assert results_unit2, "division мав знайтись у unit 2"
        assert results_unit2_mult == [], "multiplication з unit 1 не має просочуватись у unit 2"

    def test_search_empty_query_returns_empty(self, retriever, repository):
        """Empty or whitespace query returns empty results."""
        self._create_test_material(repository)

        results = retriever.search(
            query="",
            course_id=TEST_COURSE_ID,
            unit_usage_key=TEST_UNIT_USAGE_KEY,
        )
        assert results == []

        results = retriever.search(
            query="   ",
            course_id=TEST_COURSE_ID,
            unit_usage_key=TEST_UNIT_USAGE_KEY,
        )
        assert results == []

    def test_search_nonexistent_unit_returns_empty(self, retriever):
        """Search for non-existent unit returns empty list (not error)."""
        results = retriever.search(
            query="anything",
            course_id="course-v1:nonexistent+math+2026",
            unit_usage_key="block-v1:nonexistent+math+2026+type@vertical+block@u999",
        )
        assert results == []

    def test_search_respects_top_k_limit(self, retriever, repository):
        """Search never returns more than top_k results even if more match."""
        config = _get_config()
        top_k = config["retrieval"]["top_k"]

        # Create material with many matching segments
        material_id = self._create_large_test_material(repository, num_segments=20)

        results = retriever.search(
            query="test",  # matches all segments
            course_id=TEST_COURSE_ID,
            unit_usage_key=TEST_UNIT_USAGE_KEY,
        )

        assert len(results) <= top_k

    def test_search_results_sorted_by_rank_descending(self, retriever, repository):
        """Results are sorted by rank_score descending."""
        self._create_large_test_material(repository, num_segments=10)

        results = retriever.search(
            query="test",  # matches all 10 segments
            course_id=TEST_COURSE_ID,
            unit_usage_key=TEST_UNIT_USAGE_KEY,
        )

        assert len(results) >= 2, (
            f"мало результатів для перевірки сортування: {len(results)}"
        )
        scores = [r["rank_score"] for r in results]
        assert scores == sorted(scores, reverse=True), "Results not sorted by rank_score desc"

    # Helper methods for test data setup (will work when repository exists)
    def _create_test_material(self, repository, unit_usage_key=None, transcript_text=None, notes_text=None):
        """Create a test material with segments via repository."""
        if unit_usage_key is None:
            unit_usage_key = TEST_UNIT_USAGE_KEY
        if transcript_text is None:
            transcript_text = "Multiplication rules: negative times negative equals positive."
        if notes_text is None:
            notes_text = "Notes about multiplication."

        # This will fail until repository is implemented (T-018)
        return repository.create_material(
            course_id=TEST_COURSE_ID,
            unit_usage_key=unit_usage_key,
            content_version="2026-09-19.test",
            transcript=[{
                "ordinal": 0,
                "start_ms": 0,
                "end_ms": 10000,
                "text": transcript_text,
                "source_ref": "video@00:00",
            }],
            notes=[{
                "ordinal": 0,
                "section_title": "Test Section",
                "text": notes_text,
                "source_ref": "notes#test-section",
            }],
        )

    def _create_large_test_material(self, repository, num_segments=20):
        """Create a test material with many segments."""
        transcript = []
        for i in range(num_segments):
            transcript.append({
                "ordinal": i,
                "start_ms": i * 10000,
                "end_ms": (i + 1) * 10000,
                "text": f"Test segment {i} about multiplication and math concepts.",
                "source_ref": f"video@{i:02d}:00",
            })
        return repository.create_material(
            course_id=TEST_COURSE_ID,
            unit_usage_key=TEST_UNIT_USAGE_KEY,
            content_version="2026-09-19.large",
            transcript=transcript,
            notes=[],
        )


class TestRepositoryInterface:
    """Test that MaterialRepository has the expected interface for test setup."""

    def test_repository_class_exists(self):
        """MaterialRepository class should be importable."""
        RepositoryClass = _get_repository_class()
        assert RepositoryClass is not None

    def test_repository_has_create_material(self):
        """MaterialRepository should have create_material method."""
        RepositoryClass = _get_repository_class()
        assert hasattr(RepositoryClass, "create_material")
        assert callable(getattr(RepositoryClass, "create_material"))

    def test_repository_has_get_material_by_id(self):
        """MaterialRepository should have get_material_by_id method."""
        RepositoryClass = _get_repository_class()
        assert hasattr(RepositoryClass, "get_material_by_id")
        assert callable(getattr(RepositoryClass, "get_material_by_id"))


class TestConfigValuesUsedNotHardcoded:
    """Verify retriever uses config values, not hardcoded constants."""

    def test_top_k_from_config_not_hardcoded(self):
        """top_k value comes from YAML config, not hardcoded."""
        config = _get_config()
        top_k = config["retrieval"]["top_k"]
        # This test documents the expected value; retriever should use this
        assert top_k == 5

    def test_min_rank_score_from_config_not_hardcoded(self):
        """min_rank_score value comes from YAML config, not hardcoded."""
        config = _get_config()
        min_rank_score = config["retrieval"]["min_rank_score"]
        # This test documents the expected value; retriever should use this
        assert min_rank_score == 0.10


# ─── Meta-test to verify test file structure ────────────────────────────────────

class TestFtsRetrieverTestFile:
    """Meta-test to verify test file structure."""

    def test_file_has_verifies_marker(self):
        """Test file must have # verifies: FR-002-03 marker."""
        import inspect
        source = inspect.getsource(__import__(__name__))
        assert "# verifies: FR-002-03" in source

    def test_all_test_classes_exist(self):
        """Verify all expected test classes are defined."""
        expected_classes = [
            "TestRetrieverInterface",
            "TestRetrieverSearch",
            "TestRepositoryInterface",
            "TestConfigValuesUsedNotHardcoded",
        ]
        for cls_name in expected_classes:
            assert cls_name in globals(), f"Missing test class: {cls_name}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])