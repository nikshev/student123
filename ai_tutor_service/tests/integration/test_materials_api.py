# verifies: FR-002-03
"""
Integration tests for POST /materials and GET /materials/status.

Покриття (contracts/tutor-service-api.md §3–4, data-model.md §1):
- staff-only доступ: 401 без/з невірним токеном, 403 з токеном без staff-ролі;
- валідація сегментів → 400;
- ідемпотентність: той самий Idempotency-Key + payload → 201 з тим самим
  material_id і checksum; той самий key + інший payload → 409; без key → 400;
- READY лише після успішної FTS5-індексації: після 201 матеріал знаходиться
  ретривером;
- атомарний SUPERSEDED: новий content_version заміщає старий READY лише після
  успішного нового індексу;
- GET /materials/status: READY/INDEXING/FAILED/MISSING + content_version +
  segment_count + config_version, БЕЗ тексту матеріалів;
- ізоляція: пошук строго за course_id + unit_usage_key.

Кожен тест — безумовні asserts; проти заглушок T-016 падає з очікуваної
причини (501 «не реалізовано» / відсутній repository/retriever).
"""

import hashlib
import json
import uuid

import pytest
from django.test import Client

BEARER = "test-shared-secret-12345"
MATERIALS_URL = "/api/v1/materials"
STATUS_URL = "/api/v1/materials/status"

COURSE_ID = "course-v1:demo+math+2026"
UNIT_KEY = "block-v1:demo+math+2026+type@vertical+block@u1"
NEIGHBOR_UNIT_KEY = "block-v1:demo+math+2026+type@vertical+block@u2"

VALID_PAYLOAD = {
    "course_id": COURSE_ID,
    "unit_usage_key": UNIT_KEY,
    "content_version": "2026-09-19.1",
    "transcript": [
        {
            "ordinal": 0,
            "start_ms": 0,
            "end_ms": 12000,
            "text": "Welcome to the lesson on multiplication rules.",
            "source_ref": "video@00:00",
        },
        {
            "ordinal": 1,
            "start_ms": 12000,
            "end_ms": 25000,
            "text": "When multiplying two negatives, the result is positive.",
            "source_ref": "video@00:12",
        },
    ],
    "notes": [
        {
            "ordinal": 0,
            "section_title": "Multiplication Rules",
            "text": "Negative times negative equals positive.",
            "source_ref": "notes#multiplication-rules",
        },
    ],
}


def _checksum(payload: dict) -> str:
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode()).hexdigest()


def _post_materials(client: Client, payload: dict, key: str | None = None,
                    staff: bool = True, bearer: str | None = BEARER):
    headers = {}
    if bearer is not None:
        headers["HTTP_AUTHORIZATION"] = f"Bearer {bearer}"
    if staff:
        headers["HTTP_X_AI_TUTOR_ROLE"] = "staff"
    if key is not None:
        headers["HTTP_IDEMPOTENCY_KEY"] = key
    return client.post(
        MATERIALS_URL, data=json.dumps(payload),
        content_type="application/json", **headers,
    )


def _status(client: Client, course_id: str, unit_key: str):
    return client.get(
        STATUS_URL,
        data={"course_id": course_id, "unit_usage_key": unit_key},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {BEARER}",
        HTTP_X_AI_TUTOR_ROLE="staff",
    )


class TestMaterialsStaffOnly:
    """POST /materials вимагає Bearer + staff-роль."""

    def test_without_bearer_returns_401(self):
        client = Client()
        response = _post_materials(client, VALID_PAYLOAD, bearer=None,
                                   staff=False, key=str(uuid.uuid4()))
        assert response.status_code == 401

    def test_invalid_bearer_returns_401(self):
        client = Client()
        response = _post_materials(client, VALID_PAYLOAD, bearer="wrong",
                                   key=str(uuid.uuid4()))
        assert response.status_code == 401

    def test_valid_bearer_without_staff_returns_403(self):
        client = Client()
        response = _post_materials(client, VALID_PAYLOAD, staff=False,
                                   key=str(uuid.uuid4()))
        assert response.status_code == 403

    def test_staff_passes_auth_and_creates_material(self):
        client = Client()
        response = _post_materials(client, VALID_PAYLOAD, key=str(uuid.uuid4()))
        assert response.status_code == 201, (
            f"T-018 не реалізовано: очікувався 201, отримано "
            f"{response.status_code}"
        )
        data = json.loads(response.content)
        assert data["status"] == "READY"
        assert data["segment_count"] == 3
        assert data["checksum"] == _checksum(VALID_PAYLOAD)


class TestMaterialsSegmentValidation:
    """Невалідні сегменти → 400."""

    def _expect_400(self, payload: dict):
        client = Client()
        response = _post_materials(client, payload, key=str(uuid.uuid4()))
        assert response.status_code == 400, (
            f"T-018 не реалізовано: очікувався 400, отримано "
            f"{response.status_code}"
        )
        data = json.loads(response.content)
        assert "error" in data and "message" in data["error"]

    def test_transcript_start_ge_end(self):
        payload = json.loads(json.dumps(VALID_PAYLOAD))
        payload["transcript"][0]["start_ms"] = 10000
        payload["transcript"][0]["end_ms"] = 5000
        self._expect_400(payload)

    def test_transcript_negative_start_ms(self):
        payload = json.loads(json.dumps(VALID_PAYLOAD))
        payload["transcript"][0]["start_ms"] = -1000
        self._expect_400(payload)

    def test_transcript_missing_timestamps(self):
        payload = json.loads(json.dumps(VALID_PAYLOAD))
        del payload["transcript"][0]["start_ms"]
        self._expect_400(payload)

    def test_transcript_empty_text(self):
        payload = json.loads(json.dumps(VALID_PAYLOAD))
        payload["transcript"][0]["text"] = "   "
        self._expect_400(payload)

    def test_notes_missing_section_title(self):
        payload = json.loads(json.dumps(VALID_PAYLOAD))
        payload["notes"][0]["section_title"] = ""
        self._expect_400(payload)

    def test_notes_with_timestamps(self):
        payload = json.loads(json.dumps(VALID_PAYLOAD))
        payload["notes"][0]["start_ms"] = 1000
        self._expect_400(payload)

    def test_notes_empty_text(self):
        payload = json.loads(json.dumps(VALID_PAYLOAD))
        payload["notes"][0]["text"] = "  "
        self._expect_400(payload)

    def test_transcript_source_ref_not_canonical(self):
        payload = json.loads(json.dumps(VALID_PAYLOAD))
        payload["transcript"][0]["source_ref"] = "invalid-format"
        self._expect_400(payload)

    def test_notes_source_ref_not_canonical(self):
        payload = json.loads(json.dumps(VALID_PAYLOAD))
        payload["notes"][0]["source_ref"] = "invalid-format"
        self._expect_400(payload)


class TestMaterialsIdempotency:
    """Idempotency-Key + SHA-256 checksum."""

    def test_same_key_same_payload_same_response(self):
        client = Client()
        key = str(uuid.uuid4())
        r1 = _post_materials(client, VALID_PAYLOAD, key=key)
        r2 = _post_materials(client, VALID_PAYLOAD, key=key)
        assert r1.status_code == 201, (
            f"T-018 не реалізовано: очікувався 201, отримано {r1.status_code}"
        )
        assert r2.status_code == 201, (
            f"T-018 не реалізовано: повтор з тим самим key мав дати 201, "
            f"отримано {r2.status_code}"
        )
        d1, d2 = json.loads(r1.content), json.loads(r2.content)
        assert d1["material_id"] == d2["material_id"]
        assert d1["checksum"] == d2["checksum"] == _checksum(VALID_PAYLOAD)

    def test_same_key_different_payload_returns_409(self):
        client = Client()
        key = str(uuid.uuid4())
        r1 = _post_materials(client, VALID_PAYLOAD, key=key)
        assert r1.status_code == 201, (
            f"T-018 не реалізовано: очікувався 201, отримано {r1.status_code}"
        )
        other = json.loads(json.dumps(VALID_PAYLOAD))
        other["content_version"] = "2026-09-19.2"
        r2 = _post_materials(client, other, key=key)
        assert r2.status_code == 409, (
            f"T-018 не реалізовано: очікувався 409, отримано {r2.status_code}"
        )

    def test_missing_idempotency_key_returns_400(self):
        client = Client()
        response = _post_materials(client, VALID_PAYLOAD, key=None)
        assert response.status_code == 400, (
            f"T-018 не реалізовано: очікувався 400, отримано "
            f"{response.status_code}"
        )


class TestMaterialsReadyAfterFTS5:
    """READY-матеріал знаходиться ретривером (FTS5 заповнено)."""

    def test_ready_material_findable_by_search(self):
        client = Client()
        response = _post_materials(client, VALID_PAYLOAD, key=str(uuid.uuid4()))
        assert response.status_code == 201, (
            f"T-018 не реалізовано: очікувався 201, отримано "
            f"{response.status_code}"
        )
        try:
            from ai_tutor_service.config import load_tutor_config
            from ai_tutor_service.materials.retriever import MaterialRetriever
            from pathlib import Path
            config = load_tutor_config(
                Path(__file__).resolve().parent.parent.parent.parent
                / "ai_tutor_service" / "tutor_config.yaml"
            )
            retriever = MaterialRetriever(config=config)
        except ImportError as exc:
            pytest.fail(f"T-018 не реалізовано: {exc}")
        results = retriever.search("multiplication", COURSE_ID, UNIT_KEY)
        assert results, "READY-матеріал мав знаходитись пошуком"
        texts = " ".join(r["excerpt"].lower() for r in results)
        assert "negative" in texts


class TestMaterialsAtomicSuperseded:
    """Новий content_version заміщає старий READY атомарно."""

    def test_new_version_supersedes_old(self):
        client = Client()
        v1 = json.loads(json.dumps(VALID_PAYLOAD))
        v1["content_version"] = "2026-09-19.1"
        r1 = _post_materials(client, v1, key=str(uuid.uuid4()))
        assert r1.status_code == 201, (
            f"T-018 не реалізовано: очікувався 201, отримано {r1.status_code}"
        )
        material_id_v1 = json.loads(r1.content)["material_id"]

        s1 = _status(client, COURSE_ID, UNIT_KEY)
        assert s1.status_code == 200, (
            f"T-018 не реалізовано: очікувався 200, отримано {s1.status_code}"
        )
        assert json.loads(s1.content)["content_version"] == "2026-09-19.1"

        v2 = json.loads(json.dumps(VALID_PAYLOAD))
        v2["content_version"] = "2026-09-19.2"
        r2 = _post_materials(client, v2, key=str(uuid.uuid4()))
        assert r2.status_code == 201, (
            f"T-018 не реалізовано: очікувався 201, отримано {r2.status_code}"
        )

        s2 = _status(client, COURSE_ID, UNIT_KEY)
        assert s2.status_code == 200
        assert json.loads(s2.content)["content_version"] == "2026-09-19.2"

        try:
            from ai_tutor_service.materials.repository import MaterialRepository
            old = MaterialRepository().get_material_by_id(material_id_v1)
        except ImportError as exc:
            pytest.fail(f"T-018 не реалізовано: {exc}")
        assert old.status == "SUPERSEDED", (
            f"старий матеріал мав бути SUPERSEDED, отримано {old.status}"
        )


class TestMaterialsStatusEndpoint:
    """GET /materials/status."""

    def test_status_returns_expected_fields_without_text(self):
        client = Client()
        response = _post_materials(client, VALID_PAYLOAD, key=str(uuid.uuid4()))
        assert response.status_code == 201, (
            f"T-018 не реалізовано: очікувався 201, отримано "
            f"{response.status_code}"
        )
        s = _status(client, COURSE_ID, UNIT_KEY)
        assert s.status_code == 200, (
            f"T-018 не реалізовано: очікувався 200, отримано {s.status_code}"
        )
        data = json.loads(s.content)
        assert data["status"] in ("READY", "INDEXING", "FAILED", "MISSING")
        assert data["content_version"] == "2026-09-19.1"
        assert isinstance(data["segment_count"], int) and data["segment_count"] >= 0
        assert isinstance(data["config_version"], str) and data["config_version"]
        for forbidden in ("transcript", "notes", "segments", "text"):
            assert forbidden not in data, "status не має повертати текст матеріалів"

    def test_status_missing_unit_returns_missing(self):
        client = Client()
        s = _status(client, "course-v1:nonexistent+math+2026",
                    "block-v1:nonexistent+math+2026+type@vertical+block@u999")
        assert s.status_code == 200, (
            f"T-018 не реалізовано: очікувався 200, отримано {s.status_code}"
        )
        data = json.loads(s.content)
        assert data["status"] == "MISSING"
        assert data["content_version"] is None
        assert data["segment_count"] == 0

    def test_status_missing_query_params_returns_400(self):
        client = Client()
        s = client.get(
            STATUS_URL, content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {BEARER}",
            HTTP_X_AI_TUTOR_ROLE="staff",
        )
        assert s.status_code == 400, (
            f"T-018 не реалізовано: очікувався 400, отримано {s.status_code}"
        )


class TestMaterialsIsolation:
    """Пошук строго за course_id + unit_usage_key."""

    def test_search_does_not_cross_unit_boundaries(self):
        client = Client()
        unit1 = json.loads(json.dumps(VALID_PAYLOAD))
        r1 = _post_materials(client, unit1, key=str(uuid.uuid4()))
        assert r1.status_code == 201, (
            f"T-018 не реалізовано: очікувався 201, отримано {r1.status_code}"
        )

        unit2 = json.loads(json.dumps(VALID_PAYLOAD))
        unit2["unit_usage_key"] = NEIGHBOR_UNIT_KEY
        unit2["transcript"] = [{
            "ordinal": 0, "start_ms": 0, "end_ms": 10000,
            "text": "Unit 2 specific content about division.",
            "source_ref": "video@00:00",
        }]
        unit2["notes"] = []
        r2 = _post_materials(client, unit2, key=str(uuid.uuid4()))
        assert r2.status_code == 201, (
            f"T-018 не реалізовано: очікувався 201, отримано {r2.status_code}"
        )

        try:
            from pathlib import Path
            from ai_tutor_service.config import load_tutor_config
            from ai_tutor_service.materials.retriever import MaterialRetriever
            config = load_tutor_config(
                Path(__file__).resolve().parent.parent.parent.parent
                / "ai_tutor_service" / "tutor_config.yaml"
            )
            retriever = MaterialRetriever(config=config)
        except ImportError as exc:
            pytest.fail(f"T-018 не реалізовано: {exc}")

        unit1_results = retriever.search("multiplication", COURSE_ID, UNIT_KEY)
        unit1_div = retriever.search("division", COURSE_ID, UNIT_KEY)
        unit2_results = retriever.search("division", COURSE_ID,
                                         NEIGHBOR_UNIT_KEY)
        unit2_mult = retriever.search("multiplication", COURSE_ID,
                                      NEIGHBOR_UNIT_KEY)

        assert unit1_results, "multiplication мав знайтись у unit 1"
        assert unit1_div == [], "division з unit 2 не має просочуватись у unit 1"
        assert unit2_results, "division мав знайтись у unit 2"
        assert unit2_mult == [], "multiplication з unit 1 не має просочуватись у unit 2"


class TestMaterialsApiTestFile:
    """Мета-перевірка файла тесту."""

    def test_file_has_verifies_marker(self):
        import inspect
        source = inspect.getsource(__import__(__name__))
        assert "# verifies: FR-002-03" in source


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
