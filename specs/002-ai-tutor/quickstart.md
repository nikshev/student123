# Quickstart: end-to-end validation (002-ai-tutor)

**Purpose**: manual release scenarios for [spec.md](./spec.md). Automated
pytest/jest runs are deterministic and **must not use network**: service HTTP
and LLM provider responses come from committed `tests/fixtures/`. Scenarios
marked live are deliberate operator smoke/gate checks, not CI tests.

## S0. Prerequisites and offline sanity

1. Tutor single-host deploy includes `ai_tutor_xblock` and starts
   `ai_tutor_service` via `tutor-plugin/`; SQLite/FTS5 persistent volume exists.
2. Tutor secrets set `AI_TUTOR_LLM_API_KEY` and `AI_TUTOR_SHARED_SECRET` in
   Django settings for the relevant processes. Confirm neither value occurs in
   `tutor_config.yaml`, OLX export, HTML, tracking payload or application logs.
3. Committed `ai_tutor_service/tutor_config.yaml` and `gate_samples.yaml` pass
   fail-fast validation; note both versions. Service GET `/api/v1/config` over
   its internal authenticated route reports the expected public version.
4. Create/publish a test course/unit and enroll one test student; retain a
   second non-enrolled account. Add AI Tutor XBlock to the unit.
5. As staff, POST a small test transcript and notes fixture to `/materials`;
   verify `/materials/status` = READY. Include a unique fact, section title and
   transcript timecode so grounding can be distinguished from general knowledge.
6. Offline suite: run package pytest suites and jest with network disabled;
   all LLM/HTTP cases resolve from recorded fixtures. Run
   `python3 scripts/trace.py --check`; after implementation it must be green
   with `impl:`/`verifies:` markers for every FR.

## S1. In-unit answer within 30 seconds

**Covers**: FR-002-01/10, SC-001.

Enrolled student opens the published unit, asks an in-scope question and stays
on the page. Expect LOADING then answer in the same chat within 30 seconds,
`route=default`, remaining quota, no external navigation. On simulated
provider 502/timeout fixture, expect a localized ERROR with retry; same
`request_id` retry does not duplicate the turn/quota.

## S2. Grounding, source and timecode

**Covers**: FR-002-03, SC-003.

Ask about the unique fact from S0. Expect answer consistent with fixture and at
least one source such as `video@03:12` or `notes#section`; inspect response to
confirm all segment IDs belong to this course/unit. Repeat over the evaluation
set and calculate source-bearing grounded answers; target ≥90%.

## S3. No answer in materials

**Covers**: FR-002-04.

Ask an on-subject question deliberately absent from transcript/notes. Expect
NO_MATERIALS and the versioned honest message, no invented answer/sources and
no LLM candidate rendered. Remove all material from a separate fixture unit;
expect the same safe state immediately.

## S4. Off-topic question

**Covers**: spec Edge Cases, supports FR-002-03/04.

Ask an obviously unrelated question. Expect OFF_TOPIC and redirection to unit
topics, not an answer from general knowledge. Verify the low-relevance decision
used only the configured outline classifier and its output is fixture-backed in
automated tests.

## S5. Solution guard, safe reply and durable log

**Covers**: FR-002-05/06/07/08, SC-004.

Ask for a ready quiz solution using a gate fixture whose generator candidate
contains the final answer. Expect BLOCKED; candidate text never appears in DOM,
history or tracking. Student sees “Допомагаю розібратися…” and can submit a
rephrased conceptual question. Before response/event, verify append-only block
record has user/course/unit/question/time/reason/config version, then verify
`answer.blocked`. Simulated DB-log failure must return ERROR and still hide the
candidate. Target: 100% blocked outcomes have records.

## S6. Daily limit and idempotent retry

**Covers**: FR-002-11, supports SC-006.

With a test-only committed config version having a small daily limit, submit up
to the limit. Remaining decreases once per unique request. Next request returns
429/LIMIT_REACHED with explanation. Repeat a previous request with the same
idempotency key: same result, no increment. Restore production config via a new
version/changelog entry; never override limit with CLI flags.

## S7. Non-enrolled and wrong runtime

**Covers**: FR-002-02.

Open unit as non-enrolled/anonymous account: chat is unavailable and no service
request/event occurs. Force `_is_enrolled` adapter exception: same fail-closed
result. Studio preview shows author status only and cannot invoke ask/history.

## S8. Tracking events and weekly metric

**Covers**: FR-002-09, SC-005/007.

Perform shown, blocked, no-materials, 429 and simulated 503 asks. In Open edX
tracking logs expect exactly one `xblock-ai-tutor.question.asked` per unique
request with server user/course/unit, timestamp, question, topic (or `other`),
conversation ID where available and config version. Shown/blocked add their
outcome event. Compute requests per learner/week solely by distinct asked
request IDs—do not read quota table. Expect 100% accepted asks represented.

## S9. Conversation history, ownership and retention

**Covers**: FR-002-12/14.

Reload student view and request history: safe student/tutor turns remain in
unit order, including blocked rule but not blocked candidate. Try the same
conversation ID as another enrolled student and from another unit: 404-like
denial, no data. Staff access is only through audited ops/gate context. With a
short committed TTL fixture, run retention and verify expired conversation and
messages are deleted while minimized aggregate events remain.

## S10. AI-response release gate — human stop point

**Covers**: FR-002-13, SC-002, constitution AI gate.

1. Confirm committed sample/config versions and no live students enabled.
2. Offline regression first: recorded fixtures validate exact boundaries 3%,
   10% and >10% without network.
3. Authorized operator invokes live `POST /api/v1/gate/run` against the full
   pinned corpus. Technical failure makes run invalid and must be rerun.
4. Inspect report counts/rate/config/sample versions and usage logs.
5. Human records decision in `specs/002-ai-tutor/gate-decisions/`:
   rate ≤3% → may approve go; >10% → mandatory stop; (3%,10%] → explicit
   human decision. Exactly 10% is `human`, not stop; exactly 3% is `go`.
6. Do not enable live access until recorded human go exists. Any changed
   behavior/corpus version invalidates the decision and requires a new run.

## S11. Materials replacement and isolation

**Covers**: FR-002-03/04.

POST the same ingest/idempotency key twice: same material/result. Reuse key with
different payload: 409. Ingest a new content version; old remains active until
new index READY, then is superseded atomically. Ask neighboring unit for the
unique fact and verify it cannot retrieve cross-unit segments.

## S12. Cost per learner per month

**Covers**: SC-006 and constitution V metric rule.

For a representative month fixture/live pilot, aggregate service usage logs by
user and config version: token counts × versioned model rates, including
generate/guard/off-topic/gate (gate costs reported separately from learner
cost). Compare total learner generation cost with provider invoice as a
reconciliation check. Expect < USD 0.20 per learner/month; report quota usage,
model IDs and config version so unlike runs are not merged. Missing usage logs
or invoice mismatch blocks a cost claim rather than being treated as zero.

## S13. Config and secret fail-fast

**Covers**: FR-002-10/11/13/14, constitution III.

Against a disposable instance, test missing required key, invalid threshold
ordering, timeout sum ≥ request budget, secret-like YAML key and version without
matching changelog. Each must prevent healthy startup and `/ask`; no silent
default. Valid config starts, and its version appears in messages, events,
block/usage logs and gate report.
