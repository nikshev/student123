# Implementation Plan: Відео в курсах — завантаження, плеєр, події перегляду

**Branch**: `001-bunny-video` | **Date**: 2026-09-17 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-bunny-video/spec.md`

## Summary

Вчитель завантажує відео (MP4/MOV/WebM, до 2 ГБ, до 30 хв) в один крок зі
Studio без сторонніх інструментів: браузер передає файл напряму в Bunny Stream
по протоколу TUS (resumable) за серверно підписаними credentials; метадані
(ідентифікатор відео, статус, тривалість) зберігаються в полях XBlock.
Учень дивиться відео у вбудованому плеєрі Bunny за підписаним посиланням
(token + expires), яке не можна перевикористати. Плеєр емітує події
`play`/`pause`/`complete` у tracking-логи Open edX — це первинний запис
метрики «додивились до кінця». Альтернатива — вставити готове посилання
YouTube/Vimeo без токен-захисту (наявний механізм форка).

Технічний підхід: форк `raccoongang/xblock-video` (вендорений у репозиторій,
GPL-3.0) з новим бекендом `bunny` поряд із наявними YouTube/Vimeo; інтеграція
в образ edx-platform через Tutor.

## Technical Context

**Language/Version**: Python 3.11 (edx-platform у Tutor актуального релізу) —
серверна частина XBlock; JavaScript (ES5+ у стилі форка) — Studio-upload і
міст подій плеєра.

**Primary Dependencies**: XBlock SDK / edx-platform (Django); вендорений форк
`raccoongang/xblock-video`; `tus-js-client` (браузерне TUS-завантаження);
`player.js` (події плеєра Bunny через postMessage, vendored, MIT); Tutor
(збірка образу). Рішення та альтернативи — [research.md](./research.md) R1, R3, R5.

**Storage**: відео — повністю поза сервером, у Bunny Stream; на сервері —
лише метадані в полях XBlock (course OLX) і події в tracking-логах
(EVENTTRACKING). Нових таблиць БД не вводиться — див. [data-model.md](./data-model.md).

**Testing**: pytest + xblock-sdk workbench (серверна частина), jest (чиста
JS-логіка порога 95 %); усі виклики Bunny — через `BunnyApiClient` з
записаними HTTP-фікстурами (`tests/fixtures/bunny/`), тести не ходять у
мережу (принцип II).

**Target Platform**: Linux VPS (Tutor single-host, 8 ГБ RAM); браузер учня
(десктоп) та мобільний WebView — плеєр і події працюють однаково (iframe
+ postMessage), етап 2 без переробки.

**Project Type**: XBlock-плагін до edx-platform (Python + JS) + tutor-плагін
інтеграції. Не окремий веб-сервіс.

**Performance Goals**: файл до 2 ГБ іде браузер→Bunny без транзиту через VPS;
рендер студентського в'ю — один запит метаданих із XBlock (без синхронних
викликів Bunny); події перегляду — асинхронні, не блокують плеєр.

**Constraints**:
- тести без мережі, зовнішні сервіси — тільки записані фікстури;
- константи, що впливають на результат (TTL токена 24 год, поріг 95 %,
  ліміт 2 ГБ, макс. тривалість 30 хв) — у версіонованому YAML під git
  (`video_xblock/bunny_config.yaml`), не в коді і не в CLI;
- секрети Bunny (API key, token key) — тільки Django settings через Tutor,
  ніколи в course OLX;
- цикл збірки образу 10–20 хв → мінімізувати перезбірки (XBlock як
  requirements, константи в пакеті);
- CSP LMS: `frame-src https://player.mediadelivery.net`, `Referrer-Policy`
  на iframe — інакше domain-lock ламає плеєр (див. quickstart S0).

**Scale/Scope**: один VPS, ~300 учнів (PRD), одне відео на юніт, 16 FR;
аналітичний звіт і мобільний застосунок — поза скоупом фічі (етап 2 PRD).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Принцип / гейт | Статус | Обґрунтування |
|---|---|---|
| I. Трасування | PASS | FR-001-01..16 у spec.md; нові файли коду несуть `impl: FR-001-NN`, тести — `verifies: FR-001-NN`; задачі `T-xxx [FR-001-NN]` — фаза tasks.md. Зараз `trace.py --check` зелений; вендорені файли форка FR-ID не містять. |
| II. Test-First, без мережі | PASS | Дизайн ізолює Bunny за `BunnyApiClient` (єдина точка HTTP) і фікстурами; порогова логіка в чистій JS-функції під jest. Всі виклики Bunny тестуються записаними відповідями. |
| III. Константи в YAML під git | PASS | `bunny_config.yaml` з полем `version` і `changelog`; туди входять token_ttl (86400), completion_threshold (0.95), max_upload_bytes (2 ГБ), max_duration_seconds (1800), allowed_extensions. `config_version` штампується в події трекінгу й метадані відео — прогони з різними версіями розрізняються без читання коду. Контракт — contracts/bunny-config-contract.md. |
| IV. Межі модулів явні | PASS | `backends/bunny.py` — єдиний модуль, що розмовляє з Bunny REST; JS напряму ходить у Bunny лише через серверно підписаний TUS-канал (передбачений PRD); події — через handler XBlock. Інтерфейси зафіксовані в contracts/. |
| V. Метрики з подій | PASS | Події `play`/`pause`/`complete` — первинний запис метрики «додивились до кінця»; звіт (етап 2) споживає tracking-логи. Готових розв'язань AI тут нема — N/A для репетитора. |
| Гейт захисту відео | PASS (дизайн) | Кожен рендер студентського в'ю видає свіже підписане посилання (token+expires); domain-lock — налаштування бібліотеки Bunny + сценарій перевірки quickstart S13. Платні курси не відкриваються без цього — рішення людини, як вимагає конституція. |
| Гейт запуску класу | N/A | Метрика виводиться з подій цієї фічі; сам гейт спрацьовує на етапі запуску, не тут. |
| Гейт AI-відповіді | N/A | Не стосується фічі відео. |

Після Phase 1 повторна перевірка: гейти не змінилися, порушень не додано
(структура модулів і схема подій відповідають заявленим вище).

## Project Structure

### Documentation (this feature)

```text
specs/001-bunny-video/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
│   ├── bunny-api.md              # XBlock ↔ Bunny Stream REST/TUS
│   ├── xblock-interface.md       # бекенд bunny + JSON-хендлери XBlock
│   ├── tracking-events.md        # схема подій play/pause/complete
│   └── bunny-config-contract.md  # схема версіонованого YAML констант
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
video_xblock/                        # вендорений форк raccoongang/xblock-video (GPL-3.0, LICENSE збережено)
├── backends/
│   └── bunny.py                     # новий бекенд: BunnyPlayer + BunnyApiClient  (impl: FR-001-*)
├── bunny_config.yaml                # версіоновані константи (принцип III)
├── static/js/studio/bunny_upload.js # TUS-завантаження зі Studio (tus-js-client)
├── static/js/student/bunny_player.js# міст player.js → save_event (play/pause/complete)
├── static/js/lib/playerjs.min.js    # vendored player.js (MIT) — події плеєра Bunny
├── templates/bunny_student_view.html
├── templates/bunny_studio_tab.html
└── tests/
    ├── unit/                        # pytest: токени, підписи, хендлери  (verifies: FR-001-*)
    ├── js/                          # jest: порог 95 %, сесійна дедуплікація
    └── fixtures/bunny/              # записані HTTP-відповіді Bunny (без мережі)

tutor-plugin/                        # tutor-плагін: XBlock у вимоги образу, CSP, secrets
├── plugin.yml
└── patches/                         # frame-src, налаштування LMS/CMS

specs/001-bunny-video/               # документація фічі (вище)
```

**Structure Decision**: єдиний Python-пакет `video_xblock/` (форк) у корені
репозиторію + `tutor-plugin/` для розгортання. Вендоринг, а не pip-залежність
на зовнішній репозиторій, потрібен з двох причин: (1) `scripts/trace.py`
сканує саме це дерево — маркери `impl:`/`verifies:` мають бути під git тут;
(2) одна гілка = атомарні зміни форка і фічі. Окремий сервіс не потрібен —
PRD фіксує XBlock у образі edx-platform. Обґрунтування — research.md R1.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

Порушень конституції немає — секція порожня. Вендоринг GPL-3.0 форка —
не порушення: ліцензія зберігається, платформа (Open edX) сама AGPL,
використання внутрішнє (research.md R13).
