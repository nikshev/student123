# Phase 1 Data Model: AI-репетитор у контексті юніту (002-ai-tutor)

**Created**: 2026-09-19 | **Джерело**: [spec.md](./spec.md), FR-002-01..14 |
**Storage decision**: [research.md](./research.md) R3/R4/R12/R15

## Принципове рішення про зберігання

- SQLite сервісу є власником матеріалів, розмов, повідомлень, блокувань,
  лімітів та usage-логів; FTS5 — похідний індекс сегментів.
- XBlock зберігає лише авторські метадані блоку. Питання, відповіді, user ID,
  лічильники й історія **ніколи** не потрапляють у XBlock fields/course OLX.
- Tracking Open edX зберігає три типи подій через `runtime.publish`; це
  первинний запис метрики запитів, не копія добового лічильника.
- Ідентичність юніту — `unit_usage_key`; сервіс не дублює модель курсу Open edX.

## 1. Матеріал юніту та сегмент

`UnitMaterial` групує одну актуальну ingest-версію; `MaterialSegment` є
атомарною одиницею retrieval і джерелом посилання (FR-002-03/04).

| Поле | Тип | Правила |
|---|---|---|
| `id` | UUID | server-generated |
| `course_id` | String | обов'язковий; із довіреного ingest-запиту |
| `unit_usage_key` | String | обов'язковий |
| `content_version` | String | обов'язковий, унікальний у межах юніту |
| `status` | Enum `INDEXING/READY/FAILED/SUPERSEDED` | лише READY бере участь у пошуку |
| `checksum` | String | SHA-256 нормалізованого payload; idempotency |
| `created_at` | DateTime | UTC, server-generated |

| Поле сегмента | Тип | Правила |
|---|---|---|
| `id`, `material_id` | UUID, FK | каскад із material |
| `kind` | Enum `transcript/notes` | обов'язковий |
| `ordinal` | Integer | ≥0, унікальний у material |
| `text` | Text | непорожній після trim |
| `start_ms`, `end_ms` | Integer\|null | для transcript: `0 ≤ start < end`; для notes null |
| `section_title` | String\|null | для notes — непорожній; для transcript опційний |
| `source_ref` | String | канонічно `video@MM:SS` або `notes#slug` |

FTS5-рядок містить `segment_id`, `text`, `section_title`; він перебудовується
з таблиці сегментів, а не є джерелом істини. Пошук завжди фільтрується точним
`course_id + unit_usage_key` — міжюнітний витік контексту заборонений.

## 2. Розмова

| Поле | Тип | Правила |
|---|---|---|
| `id` | UUID | непередбачуваний conversation ID |
| `user_id` | String | server assertion від LMS; власник |
| `course_id` | String | незмінний після створення |
| `unit_usage_key` | String | незмінний; історія лише в межах юніту (FR-002-12) |
| `created_at`, `updated_at` | DateTime | UTC |
| `expires_at` | DateTime | `created_at + conversation_ttl_days`; > now при створенні |

Унікальність активної розмови не вимагається: учень може почати нову. Доступ:
власник із тим самим `user_id/course_id/unit_usage_key`; staff/admin — лише
через явно авторизований ops/gate контекст, що журналюється (FR-002-14).

## 3. Повідомлення

| Поле | Тип | Правила |
|---|---|---|
| `id`, `conversation_id` | UUID, FK | server-generated; належить одній розмові |
| `role` | Enum `student/tutor` | порядок задає `created_at,id` |
| `text` | Text\|null | student: непорожній; tutor: null, якщо candidate заблокований |
| `status` | Enum `asked/shown/blocked/no_materials/off_topic/error` | дозволені переходи нижче |
| `topic` | String | top source label або `other` |
| `sources` | JSON list | лише server-derived `{segment_id, kind, source_ref, excerpt}` |
| `blocked_reason` | String\|null | обов'язковий лише для `blocked` |
| `latency_ms` | Integer\|null | ≥0 для terminal tutor message |
| `route` | String | MVP тільки `default` |
| `config_version` | SemVer string | версія, що сформувала результат |
| `created_at` | DateTime | UTC |

Candidate answer до guard-перевірки не є `Message` і не зберігається в
історії учня. Для `shown` потрібне хоча б одне `source`; `no_materials` та
`off_topic` мають порожні sources і текст із версіонованого YAML.

Переходи одного turn:

```text
asked -> shown | blocked | no_materials | off_topic | error
```

Terminal status незмінний. `blocked` ніколи не переходить у `shown`; нове
переформульоване питання створює новий turn (FR-002-06/08).

## 4. Запис блокування

Окремий append-only аудит, створюється атомарно з terminal `blocked` і до
відповіді API (FR-002-07, SC-004).

| Поле | Тип | Правила |
|---|---|---|
| `id` | UUID | server-generated |
| `message_id`, `conversation_id` | UUID, FK | посилання на blocked turn |
| `user_id`, `course_id`, `unit_usage_key` | String | денормалізовані для аудиту |
| `question` | Text | точний текст запиту |
| `reason` | String | нормалізована причина guard |
| `guard_model_id` | String | із конфігурації |
| `config_version` | SemVer string | обов'язковий |
| `created_at` | DateTime | UTC; час факту блокування |

Candidate answer не входить до student history/tracking payload; для
мінімізації даних gate-аудит може зберігати його лише у контрольному звіті.

## 5. Добовий лічильник

| Поле | Тип | Правила |
|---|---|---|
| `user_id`, `date_utc` | String, Date | складений PK |
| `accepted_count` | Integer | атомарно 0..`daily_limit`; не зменшується |
| `updated_at` | DateTime | UTC |

Ліміт резервується атомарно до платного LLM-виклику. Повтор із тим самим
`request_id` не інкрементує запис; 429 повертає `daily_remaining=0`.
Лічильник — operational guard, **не** джерело тижневої метрики (FR-002-09/11).

## 6. Tracking event

Публікується XBlock; повна схема —
[contracts/tracking-events.md](./contracts/tracking-events.md).

| Поле | Тип | Правила |
|---|---|---|
| `user_id`, `course_id`, `unit_usage_key` | String | тільки server-derived LMS context |
| `request_id` | UUID | idempotency key; metric deduplicates retries |
| `event_type` | Enum `asked/shown/blocked` | узгоджений з іменем події |
| `question`, `topic` | String | question для asked; topic або `other` |
| `conversation_id` | UUID\|null | null лише якщо сервіс не створив розмову |
| `latency_ms` | Integer\|null | обов'язковий для shown |
| `blocked_reason` | String\|null | обов'язковий для blocked |
| `config_version` | SemVer string | версія сервісу; fallback остання GET /config |

Timestamp додає tracking-система. `question.asked` публікується для кожного
валідного запиту після LMS/enrollment guard, навіть якщо downstream повернув
429/5xx; це первинний запис SC-005/SC-007.

## 7. LLM usage log

Первинний запис SC-006 (витрати), append-only service log: `request_id`,
`user_id`, `model_id`, `operation` (`generate/guard/off_topic/gate`), input і
output token counts, `estimated_cost_usd`, `config_version`, timestamp.
Ціна обчислюється тільки за rates тієї ж версії YAML; provider invoice є
контрольною звіркою, не заміною первинного запису.

## 8. UI-стан чату

| Стан | Вхід | Доступна дія |
|---|---|---|
| `LOADING` | view/ask/history у роботі | чекати; подвійний submit заборонено |
| `READY` | матеріали готові, quota > 0 | поставити/переформулювати питання |
| `NO_MATERIALS` | немає індексу або відповіді в ньому | переглянути чесне пояснення |
| `OFF_TOPIC` | класифіковано поза темою | повернутися до тем юніту |
| `BLOCKED` | guard=true | прочитати правило, переформулювати |
| `LIMIT_REACHED` | 429 / remaining=0 | чекати наступної UTC-доби |
| `ERROR` | timeout/502/503/невалідна відповідь | повторити; попередній turn не дублювати |

`READY` не означає, що відповідь уже показано; текст candidate не
рендериться, доки service status не terminal `shown`.

## 9. Загальні інваріанти та трасування

1. Секрети відсутні в OLX, YAML, БД payload, tracking і логах (FR-002-14).
2. `config_version` є в terminal повідомленні, блокуванні, tracking event,
   usage log і gate report (принцип III).
3. Історія фізично видаляється після TTL; агреговані tracking events/usage
   logs зберігаються за політикою платформи без candidate answers.
4. FR mapping: матеріали/segments — FR-002-03/04; conversation/messages —
   FR-002-01/05/06/08/10/12/14; block record — FR-002-07; counter —
   FR-002-11; events/usage — FR-002-09; gate records — FR-002-13;
   enrollment identity boundary — FR-002-02 у XBlock-контракті.
