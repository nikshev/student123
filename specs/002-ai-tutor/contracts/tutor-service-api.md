# Contract: XBlock ↔ AI Tutor Service REST API

**Feature**: 002-ai-tutor | **Base path**: `/api/v1` | **Transport**: internal
HTTPS/Tutor network | **MVP route**: `default`

Це єдина поверхня між `ai_tutor_xblock` і `ai_tutor_service`. XBlock викликає
її лише через `TutorServiceClient`; LLM provider не доступний XBlock напряму.

## 1. Authentication and actor context

Кожен запит містить `Authorization: Bearer ${AI_TUTOR_SHARED_SECRET}`.
Невірний/відсутній token → 401 без деталей. Secret живе лише в Tutor
secrets/Django settings, ніколи в YAML, OLX, payload чи логах.

Для student endpoints XBlock додає server-derived headers
`X-AI-Tutor-User-ID`, `X-AI-Tutor-Course-ID`, `X-AI-Tutor-Unit-Usage-Key`.
Сервіс довіряє їм лише після Bearer auth; браузер не викликає сервіс напряму.
Для materials/gate потрібен `X-AI-Tutor-Role: staff`; роль походить із LMS/ops,
а не з request body. Немає enrollment → XBlock не викликає API (FR-002-02).

JSON error envelope для всіх endpoint-ів:

```json
{"error":{"code":"service_unavailable","message":"Репетитор тимчасово недоступний. Спробуйте ще раз."},"request_id":"uuid"}
```

## 2. POST `/ask`

Headers: actor context вище + `Idempotency-Key: <UUID>`. Request:

```json
{
  "question": "Чому при множенні двох мінусів виходить плюс?",
  "conversation_id": "uuid-or-null",
  "route": "default"
}
```

`question`: trim, непорожнє, межа `question_max_chars` із YAML; route у MVP
тільки `default`. `conversation_id=null` створює розмову в actor unit; наявний
ID мусить належати тому самому actor/course/unit, інакше 403/404 без витоку.

200 response має один із terminal status:

```json
{
  "request_id": "uuid",
  "conversation_id": "uuid",
  "status": "shown",
  "answer": "Згадай правило знаків…",
  "topic": "Правила множення",
  "sources": [
    {"segment_id":"uuid","kind":"transcript","source_ref":"video@03:12","excerpt":"…"}
  ],
  "blocked_reason": null,
  "daily_remaining": 7,
  "latency_ms": 1840,
  "route": "default",
  "config_version": "1.0.0"
}
```

| `status` | `answer` | Додаткові правила |
|---|---|---|
| `shown` | пояснення | `sources` непорожній; guard=false |
| `blocked` | версіоноване правило, не candidate | `blocked_reason` обов'язковий; block log committed |
| `no_materials` | чесний YAML template | sources=[] |
| `off_topic` | YAML template | sources=[] |

`daily_remaining` — після цього idempotent request. Candidate answer ніколи
не повертається при `blocked`. Відповідь, BlockRecord і quota reservation
фіксуються транзакційно до 200.

Статуси: 400 schema/route; 401 client auth; 403 actor mismatch; 404 невідома
conversation/unit materials identifier; 429 quota (`daily_remaining: 0` у
error details); 502 invalid/upstream LLM response; 503 service/config/DB
unavailable; 504 загальний 30-секундний budget вичерпано. Для 429/502/503/504
повідомлення локалізоване й не містить provider details.

## 3. POST `/materials`

Staff only. `Idempotency-Key` обов'язковий.

```json
{
  "course_id":"course-v1:demo+math+2026",
  "unit_usage_key":"block-v1:demo+math+2026+type@vertical+block@u1",
  "content_version":"2026-09-19.1",
  "transcript":[{"ordinal":0,"start_ms":0,"end_ms":12000,"text":"…"}],
  "notes":[{"ordinal":0,"section_title":"Правила знаків","text":"…"}]
}
```

201: `{"material_id":"uuid","status":"READY","segment_count":2,
"checksum":"sha256"}`. Той самий key+payload → та сама відповідь; той самий
key з іншим payload → 409. Новий content_version атомарно supersede-ить
попередній лише після успішної індексації. 400 — невалідні таймкоди/порожні
сегменти; 403 — не staff; 409 — version/idempotency conflict; 503 — FTS/DB.

## 4. GET `/materials/status`

Staff або authenticated XBlock. Query: `course_id`, `unit_usage_key`.
200: `{"status":"READY|INDEXING|FAILED|MISSING","content_version":"…|null",
"segment_count":42,"config_version":"1.0.0"}`. Не повертає текст матеріалів.

## 5. GET `/conversation/{id}`

Student actor context обов'язковий; повертається лише власна розмова в тому
самому course/unit. 200:

```json
{"conversation_id":"uuid","messages":[{"role":"student","text":"…","status":"asked","created_at":"…"},{"role":"tutor","text":"…","status":"shown","sources":[],"created_at":"…","config_version":"1.0.0"}],"daily_remaining":7}
```

Messages впорядковані стабільно, candidate blocked answer відсутній. 403 —
authenticated actor не власник; 404 — ID не існує/expired (однаковий envelope).

## 6. GET `/config`

Authenticated client. Повертає лише публічну проєкцію:
`{"config_version":"1.0.0","daily_limit":10,"request_timeout_seconds":30,
"http_connect_timeout_seconds":2,"question_max_chars":2000}`.
Prompts, model IDs, prices, secrets не повертаються. XBlock кешує останню
валідну версію лише для event stamping; помилка config endpoint не дозволяє
обхід fail-closed startup сервісу.

## 7. POST `/gate/run`

Staff only; `Idempotency-Key` обов'язковий. Request:
`{"sample_version":"1.0.0","route":"default"}`. Production run використовує
канонічний `gate_samples.yaml`; довільні samples у body заборонені.

200:

```json
{"run_id":"uuid","sample_version":"1.0.0","config_version":"1.0.0","total":100,"contains_solution":2,"rate":0.02,"verdict":"go","route":"default","started_at":"…","finished_at":"…"}
```

`verdict`: `go` if rate ≤0.03, `stop` if >0.10, else `human`. API не випускає
учнів і не підміняє рішення людини. 400 sample/config mismatch; 403 role;
409 duplicate conflict; 502/504 provider; 503 config/DB.

## 8. Idempotency, retries, timeout

- `/ask`, `/materials`, `/gate/run`: idempotency key зберігається з request
  hash і response. Ретраї з тим самим key безпечні; новий key — нова операція.
- Автоматичних retry у синхронному 30-секундному шляху немає: вони роблять
  latency недетермінованою. Користувацький/операторський повтор використовує
  той самий key і безпечний; 429 і 4xx не повторюються без зміни умови.
- GET можна вручну повторити; transport timeout береться з public config,
  а не hard-coded у кількох модулях.

## 9. Testability

Contract tests запускають Django test client in-process. Тести XBlock
використовують записані JSON/HTTP-фікстури `tests/fixtures/service/`; LLM
adapter — `tests/fixtures/llm/`. DNS/socket network блокується. Fixtures
покривають усі status та HTTP 200/400/401/403/404/429/502/503/504, повтор
idempotency key і malformed provider JSON.
