# Contract: AI Tutor XBlock views and JSON handlers

**Feature**: 002-ai-tutor | **Module purpose**: LMS chat UI, platform guards,
service adapter and tracking publication. XBlock does not own tutoring policy,
conversation data, LLM calls or quotas.

## 1. Trust boundary and guards

All student operations are **LMS-only** and require authenticated, active
enrollment. Guard order before service/tracking:

1. reject Studio/Workbench/non-LMS runtime for `ask`/`history`;
2. reject anonymous user;
3. call platform `_is_enrolled(user, course_id)` adapter;
4. any exception, missing course context or non-true result → deny (fail-closed).

Denied users do not receive service URL/config, do not invoke service and emit
no question event (FR-002-02). Browser-supplied user/course/unit IDs are
ignored. Studio view is author-only and never exposes student handlers/data.

## 2. `ask` json_handler

`POST /handler/ask` body:

```json
{"question":"Чому два мінуси дають плюс?","conversation_id":null,"request_id":"uuid"}
```

Validation: exact keys; UUID request/conversation where present; trimmed
non-empty question under public config limit. XBlock derives identity context,
calls `TutorServiceClient.ask` with same request ID/idempotency key, publishes
tracking events per contract and returns:

```json
{
  "status":"shown",
  "answer":"…",
  "topic":"Правила множення",
  "sources":[{"kind":"transcript","source_ref":"video@03:12","excerpt":"…"}],
  "conversation_id":"uuid",
  "daily_remaining":7,
  "latency_ms":1840,
  "route":"default",
  "config_version":"1.0.0",
  "can_retry":false
}
```

Allowed statuses: `shown`, `blocked`, `no_materials`, `off_topic`. For blocked,
`answer` is only safe rule text and sources empty. Service candidate answer is
never accepted/rendered under another status. Unknown/malformed service schema
becomes local ERROR, never best-effort display.

Local handler status mapping:

| Service/guard result | HTTP to browser | UI state/message |
|---|---|---|
| 200 shown | 200 | READY with answer/sources |
| 200 blocked | 200 | BLOCKED; rule + reformulate enabled |
| 200 no_materials/off_topic | 200 | matching state/template |
| 400 | 400 | input correction |
| 401/403 from service | 503 | generic configuration error; no auth details |
| 404 conversation | 404 | history unavailable; start new conversation |
| 429 | 429 | LIMIT_REACHED with server explanation |
| 502/503 | 503 | “Репетитор тимчасово недоступний. Спробуйте ще раз.” |
| 504/client 30 s timeout | 504 | “Не встиг відповісти. Спробуйте ще раз.” |

Every error response is JSON `{status:"error", error_code, message,
can_retry, request_id, config_version}`. Retry reuses request ID to prevent a
second quota charge/turn.

## 3. `history` json_handler

`POST /handler/history`: `{"conversation_id":"uuid"}`. Same LMS/enrollment
guards. Calls GET service conversation with server identity context.

200 response: `{conversation_id, messages, daily_remaining, config_version}`;
messages contain only student text and safe terminal tutor text/status/sources.
XBlock does not cache/persist it. Ownership 403 is returned as generic 404 to
browser to avoid exposing IDs; expired history offers a new conversation.

## 4. Student view

Server render context contains only:

| Field | Source |
|---|---|
| `config_version` | GET `/config` validated projection |
| `daily_remaining` | service quota projection (or null while loading) |
| `materials_status` | `READY/INDEXING/FAILED/MISSING` |
| handler URLs | XBlock runtime |
| initial UI state | LOADING, then mapped from material/quota status |

No secret/service base URL, prompts/model IDs, conversation messages or user ID
are embedded in HTML. UI supports LOADING/READY/NO_MATERIALS/OFF_TOPIC/BLOCKED/
LIMIT_REACHED/ERROR from data-model, disables duplicate submit, preserves typed
question after retryable error, renders source refs as text/link targets, and
announces state changes accessibly. No streaming in MVP.

## 5. Studio view

Author-only informational view: block display name; current service/config
version; material status for preview course/unit; instruction to use authorized
ingest workflow. It does not upload directly from browser, expose materials,
show student history, edit prompts/constants or include secrets.

## 6. Named module interfaces

- `TutorServiceClient`: `ask`, `history`, `config`, `materials_status`; only
  module allowed outbound HTTP. It translates transport errors to typed errors.
- `EnrollmentGuard`: `_is_enrolled` platform adapter, fail-closed and separately
  testable.
- `TrackingPublisher`: `question_asked`, `answer_shown`, `answer_blocked` over
  `runtime.publish`; validates payload before publication.
- `ChatStateReducer` (JS pure function): event + previous state → allowed UI
  state; no DOM/network side effects.

## 7. Testability

pytest handler/view tests cover LMS vs Studio, anonymous, enrolled, not
enrolled and `_is_enrolled` exception; exact API/error mappings; event order;
ownership; no persistence/secrets. Client responses are recorded fixtures and
sockets are disabled. jest covers every state transition, duplicate submit,
retry with same request ID and safe blocked rendering. Live provider/service is
never required by automated tests.
