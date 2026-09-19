# trace: ignore-file — записані відповіді AI Tutor Service (FR-002-10) є
# тестовими даними, а не ланками ланцюга трасування.
"""
Recorded responses for ``GET /api/v1/config`` (tutor-service-api.md §6).

Public projection only — no prompts, model IDs, prices, or secrets.
"""

CONFIG_200 = {
    "status_code": 200,
    "body": {
        "config_version": "1.0.0",
        "daily_limit": 10,
        "request_timeout_seconds": 30,
        "http_connect_timeout_seconds": 2,
        "question_max_chars": 2000
    }
}

CONFIG_401 = {
    "status_code": 401,
    "body": {
        "error": {
            "code": "unauthorized",
            "message": "Неавторизований запит"
        },
        "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    }
}

CONFIG_503 = {
    "status_code": 503,
    "body": {
        "error": {
            "code": "service_unavailable",
            "message": "Репетитор тимчасово недоступний. Спробуйте ще раз."
        },
        "request_id": "b2c3d4e5-f6a7-8901-bcde-f23456789012"
    }
}