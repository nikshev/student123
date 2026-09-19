# Contract: AI tutor tracking events

**Feature**: 002-ai-tutor | **Channel**: `runtime.publish(...)` → Open edX
tracking logs | **Primary metric record**: «запитів до AI на учня на тиждень»
(constitution V, FR-002-09, SC-005/007).

## 1. Event names

| Name | Emitted when |
|---|---|
| `xblock-ai-tutor.question.asked` | Valid enrolled-student ask accepted by XBlock, before/irrespective of downstream outcome |
| `xblock-ai-tutor.answer.shown` | Service returned `shown` and UI accepted the safe answer |
| `xblock-ai-tutor.answer.blocked` | Service returned `blocked`; candidate was not shown and BlockRecord exists |

`no_materials`, `off_topic`, quota and service failure still have an `asked`
event but no `shown`/`blocked`, so demand is not silently lost.

## 2. Common payload

```json
{
  "user_id": "12345",
  "course_id": "course-v1:demo+math101+2026",
  "unit_usage_key": "block-v1:demo+math101+2026+type@vertical+block@unit1",
  "request_id": "uuid",
  "event_type": "asked",
  "question": "Чому два мінуси дають плюс?",
  "topic": "Правила множення",
  "conversation_id": "uuid",
  "latency_ms": null,
  "blocked_reason": null,
  "config_version": "1.0.0"
}
```

| Field | Rules |
|---|---|
| `user_id` | server `request.user.id`; never accepted from browser JSON |
| `course_id` | server LMS context |
| `unit_usage_key` | `scope_ids.usage_id`/parent unit identity; server-derived |
| `request_id` | browser-generated UUID validated by XBlock; idempotency/deduplication key |
| `event_type` | `asked` / `shown` / `blocked`, matching event name |
| `question` | exact validated student question; present in all three for analysis |
| `topic` | for asked initially `other`; outcome has service top source label or `other` |
| `conversation_id` | for asked: supplied existing ID or null; outcome: service ID |
| `latency_ms` | required non-negative only for shown; otherwise null |
| `blocked_reason` | required non-empty only for blocked; otherwise null |
| `config_version` | service response version; on pre-response failure, last valid GET `/config` version |

Tracking adds `timestamp`; this timestamp is the authoritative time for weekly
metrics. No secrets, prompts, candidate blocked answer, provider response or
authorization token may appear in payload.

## 3. Publication semantics

- Asked is published after LMS-only, enrollment and input validation and before
  service call, so it exists even for 429/502/503/504. Delivery is at-least-once:
  duplicate handler retries reuse `request_id`; consumers count distinct IDs.
  XBlock does not add per-student storage merely to deduplicate events.
- Shown is published only after service says `shown`; its answer text is not
  tracked (data minimization). Blocked is published only after service's
  durable BlockRecord acknowledgement.
- Runtime publish failure is explicit: handler logs structured error with
  request ID and returns retryable ERROR rather than silently reporting a
  successful turn. This is critical because silent loss distorts SC-007.
- Topic in an early asked event may be `other`; downstream reporting may join
  the outcome event by conversation/request context, but never overwrites the
  immutable primary asked event.

Weekly metric = count distinct `question.asked` request IDs grouped by
`user_id` and ISO week; no daily quota table or answer event is used. Top topics
group asked/outcome records by `course_id + unit_usage_key + topic`.

## 4. Access and retention

Events follow Open edX tracking-log access policy. Question text is learner
data: only authorized analytics roles may read it; exports must preserve
course/unit filters. Retention policy is platform ops policy; it must not copy
blocked candidate answers.

## 5. Testability

Handler tests mock `runtime.publish` and assert exact name/payload for success,
blocked, no-materials, off-topic, 429 and 5xx. They verify server authority for
identity, nullable fields and no secrets/answer text. Service HTTP uses recorded
fixtures; tests disable network. A metric fixture proves two retries with one
request ID count once and that quota counters are not queried.
