# trace: ignore-file — записані відповіді AI Tutor Service (FR-002-10) є
# тестовими даними, а не ланками ланцюга трасування.
"""
Recorded responses for ``POST /api/v1/ask`` (tutor-service-api.md §2).

Covers all terminal statuses and HTTP error codes per contract.
"""

ASK_SHOWN_200 = {
    "status_code": 200,
    "body": {
        "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "conversation_id": "b2c3d4e5-f6a7-8901-bcde-f23456789012",
        "status": "shown",
        "answer": "Згадай правило знаків: при множенні двох від'ємних чисел результат додатній. Це випливає з визначення множення як повторного додавання та свойств поля дійсних чисел.",
        "topic": "Правила множення",
        "sources": [
            {
                "segment_id": "c3d4e5f6-a7b8-9012-cdef-345678901234",
                "kind": "transcript",
                "source_ref": "video@03:12",
                "excerpt": "При множенні двох мінусів виходить плюс..."
            }
        ],
        "blocked_reason": None,
        "daily_remaining": 7,
        "latency_ms": 1840,
        "route": "default",
        "config_version": "1.0.0"
    }
}

ASK_BLOCKED_200 = {
    "status_code": 200,
    "body": {
        "request_id": "b2c3d4e5-f6a7-8901-bcde-f23456789012",
        "conversation_id": "c3d4e5f6-a7b8-9012-cdef-345678901234",
        "status": "blocked",
        "answer": "Це питання містить прохання надати готове розв'язання. Я не можу дати фінальну відповідь, але можу пояснити підхід до її пошуку.",
        "topic": "Заборонена тема",
        "sources": [],
        "blocked_reason": "contains_solution",
        "daily_remaining": 6,
        "latency_ms": 2100,
        "route": "default",
        "config_version": "1.0.0"
    }
}

ASK_NO_MATERIALS_200 = {
    "status_code": 200,
    "body": {
        "request_id": "c3d4e5f6-a7b8-9012-cdef-345678901234",
        "conversation_id": "d4e5f6a7-b8c9-0123-defa-456789012345",
        "status": "no_materials",
        "answer": "На жаль, для цього юніту ще не завантажено навчальних матеріалів, тому я не можу дати обґрунтовану відповідь. Зверніться до викладача або спробуйте пізніше.",
        "topic": None,
        "sources": [],
        "blocked_reason": None,
        "daily_remaining": 5,
        "latency_ms": 450,
        "route": "default",
        "config_version": "1.0.0"
    }
}

ASK_OFF_TOPIC_200 = {
    "status_code": 200,
    "body": {
        "request_id": "d4e5f6a7-b8c9-0123-defa-456789012345",
        "conversation_id": "e5f6a7b8-c9d0-1234-efab-567890123456",
        "status": "off_topic",
        "answer": "Це питання виходить за межі теми поточного уроку. Я можу допомогти лише з матеріалами цього юніту. Спробуйте перефразувати питання в контексті теми уроку.",
        "topic": None,
        "sources": [],
        "blocked_reason": None,
        "daily_remaining": 4,
        "latency_ms": 520,
        "route": "default",
        "config_version": "1.0.0"
    }
}

ASK_400 = {
    "status_code": 400,
    "body": {
        "error": {
            "code": "invalid_request",
            "message": "Невалідний запит: question не може бути порожнім"
        },
        "request_id": "e5f6a7b8-c9d0-1234-efab-567890123456"
    }
}

ASK_401 = {
    "status_code": 401,
    "body": {
        "error": {
            "code": "unauthorized",
            "message": "Неавторизований запит"
        },
        "request_id": "f6a7b8c9-d0e1-2345-fabc-678901234567"
    }
}

ASK_403 = {
    "status_code": 403,
    "body": {
        "error": {
            "code": "forbidden",
            "message": "Доступ заборонено: conversation_id не належить цьому актору"
        },
        "request_id": "a7b8c9d0-e1f2-3456-abcd-789012345678"
    }
}

ASK_404 = {
    "status_code": 404,
    "body": {
        "error": {
            "code": "not_found",
            "message": "Розмову не знайдено або матеріали юніту відсутні"
        },
        "request_id": "b8c9d0e1-f2a3-4567-bcde-890123456789"
    }
}

ASK_429 = {
    "status_code": 429,
    "body": {
        "error": {
            "code": "quota_exceeded",
            "message": "Щоденний ліміт запитів вичерпано. Спробуйте завтра.",
            "details": {"daily_remaining": 0}
        },
        "request_id": "c9d0e1f2-a3b4-5678-cdef-901234567890"
    }
}

ASK_502 = {
    "status_code": 502,
    "body": {
        "error": {
            "code": "service_unavailable",
            "message": "Репетитор тимчасово недоступний. Спробуйте ще раз."
        },
        "request_id": "d0e1f2a3-b4c5-6789-def0-012345678901"
    }
}

ASK_503 = {
    "status_code": 503,
    "body": {
        "error": {
            "code": "service_unavailable",
            "message": "Репетитор тимчасово недоступний. Спробуйте ще раз."
        },
        "request_id": "e1f2a3b4-c5d6-7890-ef01-123456789012"
    }
}

ASK_504 = {
    "status_code": 504,
    "body": {
        "error": {
            "code": "timeout",
            "message": "Час очікування відповіді вичерпано. Спробуйте ще раз."
        },
        "request_id": "f2a3b4c5-d6e7-8901-f012-234567890123"
    }
}

ASK_MALFORMED_JSON = {
    "status_code": 200,
    "body": "not a valid json response {{{",
    "raw": True  # Indicates this is raw malformed response
}