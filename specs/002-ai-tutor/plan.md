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

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

Порушень конституції немає — секція порожня.
