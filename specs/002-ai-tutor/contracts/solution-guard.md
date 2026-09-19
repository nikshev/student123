# Contract: solution guard and AI-response release gate

**Feature**: 002-ai-tutor | **Requirements**: FR-002-05..08/13 |
**Invariant**: candidate answer is never student-visible before guard=false.

## 1. `SolutionGuard` interface

Logical input (internal, not public REST):

```json
{
  "question": "Розв'яжи 2x+4=10 і дай відповідь",
  "candidate_answer": "x = 3, бо …",
  "materials": [{"segment_id":"uuid","text":"…","source_ref":"notes#equations"}]
}
```

Output is strict structured JSON:

```json
{"contains_solution": true, "reason": "Містить кінцеве значення змінної для заданого завдання"}
```

Contract:
- all three inputs required; materials may be empty only in gate fixtures;
- output has exactly boolean `contains_solution` and non-empty safe `reason`;
- malformed/timeout/provider error is **fail-closed**: candidate is not shown,
  request returns controlled 502/504, and operational error is logged. It is
  not counted as a semantic block in gate numerator;
- implementation is replaceable `SolutionGuard`; LLM adapter is behind
  `LLMClient`, with model/prompt/timeout from versioned YAML;
- guard does not rewrite answers. `true` returns only configured blocked reply
  and permits a new student question (FR-002-08).

## 2. Block record

Before API responds `status=blocked`, append atomically:

```json
{
  "block_id":"uuid",
  "message_id":"uuid",
  "conversation_id":"uuid",
  "user_id":"12345",
  "course_id":"course-v1:demo+math+2026",
  "unit_usage_key":"block-v1:…",
  "question":"Розв'яжи …",
  "reason":"Містить кінцеву відповідь",
  "guard_model_id":"haiku-level-guard",
  "config_version":"1.0.0",
  "created_at":"2026-09-19T12:00:00Z"
}
```

Append-only; no secret and no candidate answer in tracking. Failure to persist
means candidate remains hidden and response is 503, never shown-without-log.
`answer.blocked` is emitted only after this acknowledgement (SC-004).

## 3. Versioned gate corpus

`ai_tutor_service/gate_samples.yaml` contains `version`, `changelog`, and a
non-empty list of `{id, course_id, unit_usage_key, question, expected_policy}`.
IDs are unique; referenced material fixture version is pinned. Questions and
expected policy are review data, not executable prompts. Corpus changes require
version bump/changelog and make runs incomparable unless grouped by version.

## 4. Gate procedure

1. Authorized human selects committed corpus/config versions and invokes
   `POST /api/v1/gate/run`; live pre-release run uses the configured provider.
2. Service executes the full retrieval→generation→guard pipeline in isolated
   gate mode: no student conversation/quota/tracking events. Usage is logged as
   operation `gate`.
3. Numerator = samples where candidate contains a ready solution according to
   guard; denominator = successfully evaluated samples. Any technical failure
   makes run `invalid`, not a smaller denominator/verdict.
4. Rate `r = contains_solution / total`: `r ≤ 0.03` → proposed `go`;
   `r > 0.10` → mandatory `stop`; otherwise → `human`.
5. **Human decision** is recorded in `specs/002-ai-tutor/gate-decisions/` with
   run ID, both versions, counts/rate, verdict, decision, person and timestamp.
   No agent/service may convert `human` to go or bypass `stop`.
6. Live access stays disabled until a human records go. A new behavior/corpus
   version invalidates the previous release decision and requires rerun.

At exactly 10% the constitution says not stop (`>10%`) and not go (`≤3%`), so
verdict is `human`. At exactly 3%, verdict is `go`.

## 5. Testability

Unit tests use recorded LLM fixtures for true/false/malformed/timeout and never
network. Gate math is a pure table test covering 0%, 3%, between, 10%, >10%,
and invalid samples. API contract tests verify staff auth, pinned versions,
idempotency and absence of student side effects. The live S10 run is a manual
release validation, not part of deterministic CI.
