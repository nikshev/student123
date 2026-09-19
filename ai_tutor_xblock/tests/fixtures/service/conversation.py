# trace: ignore-file — записані відповіді AI Tutor Service (FR-002-10) є
# тестовими даними, а не ланками ланцюга трасування.
"""
Recorded responses for ``GET /api/v1/conversation/{id}`` (tutor-service-api.md §5).

Student actor context required; returns only own conversation in same course/unit.
"""

CONVERSATION_200 = {
    "status_code": 200,
    "body": {
        "conversation_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "messages": [
            {
                "role": "student",
                "text": "Чому при множенні двох мінусів виходить плюс?",
                "status": "asked",
                "created_at": "2026-09-19T10:00:00.000Z"
            },
            {
                "role": "tutor",
                "text": "Згадай правило знаків: при множенні двох від'ємних чисел результат додатній. Це випливає з визначення множення як повторного додавання та свойств поля дійсних чисел.",
                "status": "shown",
                "sources": [
                    {
                        "segment_id": "c3d4e5f6-a7b8-9012-cdef-345678901234",
                        "kind": "transcript",
                        "source_ref": "video@03:12",
                        "excerpt": "При множенні двох мінусів виходить плюс..."
                    }
                ],
                "created_at": "2026-09-19T10:00:05.000Z",
                "config_version": "1.0.0"
            },
            {
                "role": "student",
                "text": "А чому так?",
                "status": "asked",
                "created_at": "2026-09-19T10:01:00.000Z"
            },
            {
                "role": "tutor",
                "text": "Це можна зрозуміти, якщо подивитися на числову пряму: множення на -1 — це відбиття відносно нуля. Два відбиття повертають у вихідну точку.",
                "status": "shown",
                "sources": [
                    {
                        "segment_id": "d4e5f6a7-b8c9-0123-defa-456789012345",
                        "kind": "notes",
                        "source_ref": "notes#rules-of-signs",
                        "excerpt": "Множення на -1 — відбиття відносно нуля"
                    }
                ],
                "created_at": "2026-09-19T10:01:08.000Z",
                "config_version": "1.0.0"
            }
        ],
        "daily_remaining": 7
    }
}

CONVERSATION_401 = {
    "status_code": 401,
    "body": {
        "error": {
            "code": "unauthorized",
            "message": "Неавторизований запит"
        },
        "request_id": "b2c3d4e5-f6a7-8901-bcde-f23456789012"
    }
}

CONVERSATION_403 = {
    "status_code": 403,
    "body": {
        "error": {
            "code": "forbidden",
            "message": "Доступ заборонено: не власник розмови"
        },
        "request_id": "c3d4e5f6-a7b8-9012-cdef-345678901234"
    }
}

CONVERSATION_404 = {
    "status_code": 404,
    "body": {
        "error": {
            "code": "not_found",
            "message": "Розмову не знайдено"
        },
        "request_id": "d4e5f6a7-b8c9-0123-defa-456789012345"
    }
}

CONVERSATION_503 = {
    "status_code": 503,
    "body": {
        "error": {
            "code": "service_unavailable",
            "message": "Репетитор тимчасово недоступний. Спробуйте ще раз."
        },
        "request_id": "e5f6a7b8-c9d0-1234-efab-567890123456"
    }
}