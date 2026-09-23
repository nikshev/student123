# Implementation Plan: AI-репетитор у контексті юніту

**Branch**: `002-ai-tutor` | **Date**: 2026-09-19 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-ai-tutor/spec.md`

## Summary

Зарахований учень ставить питання в чаті всередині юніту й протягом 30 с
отримує пояснення, прив'язане до транскрипту/конспекту, або чесну відмову.
Готові розв'язання блокуються окремим класифікатором до показу; кожен запит і
блокування залишають первинний запис для метрик та гейту.

Технічний підхід: тонкий `ai_tutor_xblock/` відповідає лише за UI, LMS-гварди,
події та HTTP-клієнт; окрема Django-апка `ai_tutor_service/` тримає SQLite
FTS5 RAG, розмови, ліміт, LLM/guard, ingest і gate API. Поведінкові константи
та промпти мають одне джерело істини — версіонований `tutor_config.yaml`.

## Technical Context

**Language/Version**: Python 3.11 для XBlock і Django-сервісу; JavaScript для
чат-UI в стилі XBlock SDK.

**Primary Dependencies**: XBlock SDK / edx-platform runtime; Django + Django
ORM; SQLite з FTS5; HTTP-клієнт за інтерфейсом `TutorServiceClient`; LLM
провайдер за інтерфейсом `LLMClient`; Tutor для розгортання.

**Storage**: SQLite на постійному томі сервісу: нормалізовані матеріали,
FTS5-індекс, розмови/повідомлення, блокування, добові лічильники та usage-логи.
Tracking-події — у стандартному EVENTTRACKING Open edX. XBlock не зберігає
пер-студентські дані.

**Testing**: pytest для обох Python-пакетів (unit/contract/integration), jest
для чистої UI state-machine. Всі LLM та HTTP-виклики — лише записані фікстури
в `tests/fixtures/`; тестам заборонена мережа.

**Target Platform**: Linux VPS, Tutor single-host (8 ГБ RAM / 4 CPU / 50 ГБ);
LMS у браузері та мобільному WebView. Сервіс — окремий внутрішній процес.

**Project Type**: два Python-пакети — XBlock web component + внутрішня Django
web-service app — та розширення tutor-плагіна.

**Performance Goals**: звичайна відповідь ≤ 30 с; синхронний pipeline має
загальний тайм-бюджет; FTS5 top-k по одному юніту; медіана ≤ 30 с (SC-001).

**Constraints**:
- готове розв'язання ніколи не показується до guard-перевірки; блокування
  журналюється, а реліз зупиняється за AI-гейтом;
- константи, промпти, пороги, ліміти, таймаути, retention і ціни для оцінки
  витрат — у `ai_tutor_service/tutor_config.yaml` з version/changelog;
- секрети `AI_TUTOR_LLM_API_KEY` і `AI_TUTOR_SHARED_SECRET` — лише Tutor
  secrets → Django settings, не в YAML/OLX/подіях;
- синхронний MVP без стримінгу; `route="default"`; без модельного роутингу;
- fail-closed LMS-only і `_is_enrolled`; зовнішні системи лише за інтерфейсами;
- персональні дані мінімізуються та видаляються після конфігурованого TTL.

**Scale/Scope**: ~300 учнів, десятки юнітів одного предмета, 5+ запитів на
учня на тиждень; single-writer SQLite прийнятний. Дашборд топ-тем,
embeddings/pgvector, streaming і model routing — етап 2.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Принцип / гейт | Статус | Обґрунтування |
|---|---|---|
| I. Трасування | PASS | FR-002-01..14 відображені в контрактах і data-model. У майбутньому `tasks.md`: кожен рядок `T-0NN [FR-002-NN]`; код має `impl: FR-002-NN`, тести — `verifies: FR-002-NN`; `scripts/trace.py` замикає ланцюг. |
| II. Test-First, без мережі | PASS | `TutorServiceClient` і `LLMClient` — межі зовнішніх систем; pytest/jest використовують записані HTTP/LLM-фікстури, network-disabled suite. Quickstart відділяє offline automation від ручного live smoke. |
| III. Константи в YAML під git | PASS | Єдиний `tutor_config.yaml` містить prompts/model IDs/пороги/ліміти/таймаути/retrieval/retention/cost rates, має `version`+`changelog`; `config_version` є в повідомленнях, логах, gate report і tracking events. Gate samples — окремий версіонований YAML. |
| IV. Межі модулів явні | PASS | XBlock: UI+LMS trust boundary+tracking; сервіс: tutoring pipeline+storage. HTTP, LLM, retrieval, guard, config та tracking мають названі інтерфейси й окремі contract tests. |
| V. AI навчає; метрики з подій | PASS | Guard перевіряє candidate до показу, `BlockRecord` і `answer.blocked` фіксують факт. `question.asked` — первинний запис 5+ запитів/тиждень; LLM usage log — первинний запис витрат. |
| Гейт AI-відповіді | PASS (дизайн) | `gate_samples.yaml` + `POST /api/v1/gate/run`: >10% `stop`, ≤3% `go`, між ними `human`; лише людина записує рішення перед живим запуском (quickstart S10). |
| Гейт запуску класу | N/A | Належить запуску класу/відеометриці, не цій фічі. |
| Гейт захисту відео | N/A | Належить 001-bunny-video. |

Після Phase 1 повторна перевірка: контракти зберігають усі статуси до показу,
явно забороняють мережу в тестах і дають первинні записи для SC-005/SC-006;
нових порушень не з'явилося.

## Project Structure

### Documentation (this feature)

```text
specs/002-ai-tutor/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
│   ├── tutor-service-api.md
│   ├── tracking-events.md
│   ├── tutor-config-contract.md
│   ├── solution-guard.md
│   └── xblock-interface.md
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)
```text
ai_tutor_xblock/
├── ai_tutor_xblock/
│   ├── block.py                 # render + ask/history handlers + LMS/enrollment guards
│   ├── client.py                # TutorServiceClient HTTP adapter
│   ├── static/js/ai_tutor.js    # chat state machine and accessible UI
│   └── templates/               # student/studio views
└── tests/
    ├── unit/
    ├── contract/
    ├── js/
    └── fixtures/service/        # recorded service responses; no network

ai_tutor_service/
├── tutor_config.yaml            # all behavior constants, version + changelog
├── gate_samples.yaml            # versioned release-gate corpus
├── api/                         # REST views/serializers/auth; no tutoring policy
├── conversations/               # history, ownership, retention
├── materials/                   # ingest + FTS5 repository/retriever
├── tutoring/                    # orchestration, prompts, routes/statuses
├── guard/                       # SolutionGuard interface + LLM adapter + gate evaluator
├── limits/                      # atomic daily quota
├── providers/                   # LLMClient adapter and usage logging
└── tests/
    ├── unit/
    ├── contract/
    ├── integration/
    └── fixtures/llm/            # recorded provider payloads; no network

tutor-plugin/
├── plugin.yml                   # install/start service and XBlock
└── patches/                     # service URL, settings, secrets, persistent volume

specs/002-ai-tutor/              # design and future tasks.md
```

**Structure Decision**: два пакети потрібні через різні trust/runtime boundaries:
XBlock знає Open edX user/course/unit і публікує події, але не зберігає чат і
не викликає LLM; сервіс не залежить від XBlock SDK і володіє pipeline/даними.
Кожен підмодуль має одну мету й контракт. `tutor-plugin/` є лише deployment
adapter. Обґрунтування — [research.md](./research.md) R1–R7/R15.

## Поправка 2026-09-23: unknown ≠ zero в LLM usage log (FR-002-14)

Мале spec-зміна без зміни trust boundaries. Проблема: `record_usage` писав
`0/0/Decimal("0")` коли usage невідомий, змішуючи «unknown cost» з «zero cost»
і занижуючи SC-006. Рішення (затверджений scope): всі три колонки
`LLMUsageLog.input_tokens/output_tokens/estimated_cost_usd` стають nullable;
NULL = unknown; агрегація пропускає NULL.

### Міграційний підхід

- `ai_tutor_service/providers/migrations/0002_usage_nullable.py`:
  `AlterField` ×3 (`input_tokens`, `output_tokens` → `PositiveIntegerField`
  `null=True, blank=True`; `estimated_cost_usd` → `DecimalField(12,6)`
  `null=True, blank=True`). PK, FK, індекси, `config_version`/`request_id` —
  без змін. `makemigrations --check` мусить бути чистим після задачі.
- `ai_tutor_service/providers/migrations/0003_backfill_unknown_usage.py`:
  `RunPython(forwards, backwards)` через `apps.get_model` (без raw-DDL):
  forwards NULL-ить рядки за правилом нижче; backwards пише `0/0/0.000000`
  **усім** NULL-рядкам (інакше зворотний `AlterField` на NOT NULL впаде —
  свідомо lossy, задокументувати в docstring міграції). Обидва напрями
  ідемпотентні (повторний прогін не змінює вже конвертовані рядки).

### DATA-LOSS DECISION REQUIRED — обране backfill-правило (консервативне)

Forwards конвертує `0 → NULL` **лише** при одночасному виконанні всіх чотирьох
умов (AND):

```text
input_tokens = 0 AND output_tokens = 0
AND estimated_cost_usd = 0
AND created_at < 2026-06-01
```

Чому це безпечно: це рівно той відбиток, який могла лишити стара unknown-гілка
`record_usage` (писала три нулі й нічого іншого). Genuine zero у даних до
cutoff нерозрізнюваний з unknown у принципі, тому рішення береться за часом
(`created_at`), а не за вмістом. Альтернативне прочитання «все до переходу на
NULL-семантику вважати unknown» відкинуте: воно стерло б свіжі genuine-zero
рядки, насамперед `operation=gate` (сьогодні вже викликається з
`input_tokens=None, output_tokens=None`). Частковий usage (відомий лише один
token count) рахується з відомої частини: відомий лічильник зберігається,
невідомий — NULL, вартість обчислюється з відомої частини (див.
data-model.md §7).

Фактичний наслідок для цього репо: `0001_initial` датована 2026-09-19, рядків
із `created_at < 2026-06-01` не існує — backfill на поточному дампі конвертує
**нуль рядків**, зміна де-факто schema-only. Правило все одно потрібне для
прод-дампів, скопійованих зі старих середовищ.

### Cutoff-константа: виняток із принципу III (обґрунтування)

`USAGE_UNKNOWN_BACKFILL_CUTOFF = date(2026, 6, 1)` живе в
`ai_tutor_service/providers/models.py` як іменована константа модуля, **не** в
`tutor_config.yaml`. Принцип III покриває константи, що змінюють висновок на
кожному прогоні (ліміти, пороги, таймаути, **cost rates — лишаються в YAML**).
Cutoff не налаштовує поведінку: це історичний факт про один уже застосований
data-fix, який читає рівно один споживач — міграція `0003`, стан якої вже
записаний у `django_migrations`. У YAML він виглядав би переналаштовуваним,
хоч після `migrate` змінити його неможливо. Порівнюваність прогонів
забезпечує `config_version`, як і раніше. Міграція імпортує константу, не
дублює літерал; тотожність літерала константі фіксує тест (T-057).

### Маркери трасування (щоб `scripts/trace.py` лишився зеленим)

- `ai_tutor_service/providers/models.py` лишається `# impl: FR-002-14`.
- `ai_tutor_service/providers/usage.py` → `# impl: FR-002-09, FR-002-14`
  (запис/агрегація історично FR-002-09, NULL-семантика — FR-002-14;
  `trace.py` підтримує список через кому, ланцюг замкнений для обох FR).
- `ai_tutor_service/tests/unit/test_usage_log.py` →
  `# verifies: FR-002-09, FR-002-14`; новий
  `ai_tutor_service/tests/unit/test_usage_backfill_migration.py` →
  `# verifies: FR-002-14`.
- Розбіжність доки/коду в `usage.py` (docstring обіцяє `None`, код пише `0`)
  виправляється в T-060 приведенням docstring і коду до NULL-семантики.

### Superset / dataset docs — нотатка (нових `.md` не створюємо)

Окремих Superset/dataset доків у `docs/` немає (лише `PRD-open-edx.md` і
`traceability.md`). Коли вони з'являться, зафіксувати:

```text
cost = SUM(estimated_cost_usd)
       WHERE estimated_cost_usd IS NOT NULL AND operation <> 'gate'
       GROUP BY config_version;
unknown_cnt = COUNT(*) WHERE estimated_cost_usd IS NULL;
```

Фільтр лишити явним, хоч `SUM` ігнорує NULL сам. Поруч з cost-дашбордом
обов'язкова метрика `unknown_cnt`. `COALESCE(tokens, 0)` / `COALESCE(cost, 0)`
заборонені — вони відновлюють саме ту помилку, яку знімає поправка. Розріз
завжди по `config_version`; pre-2026-06-01 зрізи позначати «містять unknown».

### Повторна перевірка конституції

- I. Трасування: PASS — подвійні маркери закривають FR-002-09 і FR-002-14.
- II. Test-First/без мережі: PASS — усі нові тести на ORM/фікстурах, socket/DNS
  заборонені як і раніше.
- III. YAML: PASS з обґрунтованим винятком — rates лишаються в YAML, cutoff —
  історичний факт у модулі (див. вище).
- IV. Межі: PASS — запис usage лишається єдиною відповідальністю
  `providers/`; quota (`limits/`) не є джерелом метрики.
- V. Метрики з подій: PASS і посилено — `count_unknown_usage()` робить
  пропуски видимими, SC-006 не занижується мовчки.
- Секрети: PASS — міграції й тести не торкаються секретів.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

Порушень конституції немає — секція порожня.
