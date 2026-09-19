# trace: ignore-file — записані відповіді AI Tutor Service (FR-002-10) є
# тестовими даними, а не ланками ланцюга трасування.
"""
Recorded responses for ``GET /api/v1/materials/status`` (tutor-service-api.md §4).
"""

MATERIALS_STATUS_READY = {
    "status_code": 200,
    "body": {
        "status": "READY",
        "content_version": "2026-09-19.1",
        "segment_count": 42,
        "config_version": "1.0.0"
    }
}

MATERIALS_STATUS_INDEXING = {
    "status_code": 200,
    "body": {
        "status": "INDEXING",
        "content_version": "2026-09-19.2",
        "segment_count": 0,
        "config_version": "1.0.0"
    }
}

MATERIALS_STATUS_FAILED = {
    "status_code": 200,
    "body": {
        "status": "FAILED",
        "content_version": "2026-09-18.5",
        "segment_count": 0,
        "config_version": "1.0.0"
    }
}

MATERIALS_STATUS_MISSING = {
    "status_code": 200,
    "body": {
        "status": "MISSING",
        "content_version": None,
        "segment_count": 0,
        "config_version": "1.0.0"
    }
}

MATERIALS_STATUS_400 = {
    "status_code": 400,
    "body": {
        "error": {
            "code": "invalid_request",
            "message": "Відсутні обов'язкові параметри: course_id, unit_usage_key"
        },
        "request_id": "c3d4e5f6-a7b8-9012-cdef-345678901234"
    }
}

MATERIALS_STATUS_401 = {
    "status_code": 401,
    "body": {
        "error": {
            "code": "unauthorized",
            "message": "Неавторизований запит"
        },
        "request_id": "d4e5f6a7-b8c9-0123-defa-456789012345"
    }
}

MATERIALS_STATUS_403 = {
    "status_code": 403,
    "body": {
        "error": {
            "code": "forbidden",
            "message": "Доступ заборонено: потрібна роль staff"
        },
        "request_id": "e5f6a7b8-c9d0-1234-efab-567890123456"
    }
}

MATERIALS_STATUS_503 = {
    "status_code": 503,
    "body": {
        "error": {
            "code": "service_unavailable",
            "message": "Репетитор тимчасово недоступний. Спробуйте ще раз."
        },
        "request_id": "f6a7b8c9-d0e1-2345-fabc-678901234567"
    }
}