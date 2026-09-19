# Contract: `ai_tutor_service/tutor_config.yaml`

**Feature**: 002-ai-tutor | **Principle**: constitution III. Це єдине джерело
поведінкових констант сервісу та XBlock public projection.

## 1. Loading and fail-fast

Файл пакується із сервісом, читається на startup і валідовується повністю до
прийому traffic. Відсутній/unknown key, неправильний тип/межа, unresolved env,
дубль version або changelog mismatch → process unhealthy, `/ask` не працює.
Жодних тихих defaults у коді. Secrets у файлі заборонені.

## 2. Canonical shape

```yaml
version: 1.0.0
daily_limit: 10
conversation_ttl_days: 30
question_max_chars: 2000
request_timeout_seconds: 30
generation_timeout_seconds: 18
guard_timeout_seconds: 7
http_connect_timeout_seconds: 2
model_id: haiku-level-model
guard_model_id: haiku-level-guard
retrieval:
  top_k: 5
  min_rank_score: 0.10
guard:
  gate_go_threshold: 0.03
  gate_stop_threshold: 0.10
prompts:
  tutor: "…"
  guard: "…"
  off_topic: "…"
  no_materials: "…"
replies:
  blocked: "Допомагаю розібратися, а не розв'язую за тебе."
  off_topic: "Повернімося до тем цього уроку."
  no_materials: "У матеріалах цього уроку відповіді немає."
cost:
  currency: USD
  input_per_million_tokens: 0.25
  output_per_million_tokens: 1.25
changelog:
  - version: 1.0.0
    date: 2026-09-19
    changes: ["Початкова конфігурація MVP"]
```

Values вище — дизайн defaults; реалізація створює саме YAML, не дублює їх у
Python/JS/CLI.

## 3. Schema and bounds

| Key | Type | Validation |
|---|---|---|
| `version` | SemVer string | required; equals newest changelog entry |
| `changelog` | list | non-empty `{version,date,changes[]}`; unique ascending versions |
| `daily_limit` | int | ≥1 |
| `conversation_ttl_days` | int | >0 |
| `question_max_chars` | int | ≥1 |
| `request_timeout_seconds` | number | >0 and ≤30 |
| `generation_timeout_seconds`, `guard_timeout_seconds` | number | >0; sum < request timeout |
| `http_connect_timeout_seconds` | number | >0 and < request timeout |
| `model_id`, `guard_model_id` | non-empty string | provider identifiers; not secrets |
| `retrieval.top_k` | int | ≥1 |
| `retrieval.min_rank_score` | number | 0 < x ≤1 (service normalizes FTS score to this range) |
| gate thresholds | number | `0 < go ≤ stop ≤1`; canonical 0.03/0.10 |
| four `prompts.*` | non-empty string | required; structured output instructions for guard/off_topic |
| three `replies.*` | non-empty string | safe student-facing text |
| `cost.currency` | enum | MVP `USD` |
| cost rates | number | ≥0; per 1,000,000 tokens |

`prompts.no_materials` may define outline-based decision context but must not
instruct generation from general knowledge. `replies` are deterministic output;
prompts are provider instructions. Secret-like keys (`api_key`, `secret`,
`token`) fail validation even if unknown-key checks would already reject them.

## 4. Version/changelog procedure

Any value affecting output, gate verdict, retention, quota, timing or cost
measurement requires in one commit: version bump, newest changelog entry with
reason, fixture expectation updates. Semantic versioning: major for schema/
meaning change, minor for behavioral change, patch for wording correction that
still changes output. Historical entries are immutable and never removed.

`config_version` is stamped into terminal messages, block/usage logs, gate
reports and all tracking events. Runs with different versions are not compared
without explicit grouping.

`gate_samples.yaml` independently has `version`, `changelog`, and non-empty
samples; changing corpus bumps its version even when tutor config is unchanged.

## 5. Testability

Table-driven pytest validates every boundary, unknown/missing key, threshold
ordering, time-budget sum, secret-like key and changelog mismatch. Snapshot
tests load the committed YAML. No test invokes network or a live LLM.
