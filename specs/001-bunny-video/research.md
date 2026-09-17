# Phase 0 Research: Відео в курсах (001-bunny-video)

**Created**: 2026-09-17 | **Input**: [spec.md](./spec.md), [plan.md](./plan.md), PRD §Технічна архітектура/§Відео

Закриття невідомих Technical Context. Формат кожного пункту:
**Decision / Rationale / Alternatives considered**.
Джерела: офіційна документація Bunny Stream (docs, 2026), код
`raccoongang/xblock-video` (base.py бекендів), конституція проекту.

## Зведена таблиця «невідоме → рішення»

| # | Невідоме (з Technical Context) | Рішення |
|---|---|---|
| R1 | Як брати форк: pip з GitHub чи вендоринг | Вендоринг у репозиторій (`video_xblock/`) |
| R2 | Як XBlock розпізнає Bunny-відео (url_re не застосовний) | Явний `player_name='bunny'` + метадані; url_re порожній |
| R3 | Протокол завантаження 2 ГБ з resume | TUS напряму з браузера (tus-js-client), серверний presign |
| R4 | Де перевіряти ліміти розміру/формату/тривалості | Розмір/формат — до передавання (JS + сервер); тривалість — після енкодингу, з видаленням |
| R5 | Який плеєр: свій video.js+HLS чи embed Bunny | Embed-плеєр Bunny + vendored player.js (події postMessage) |
| R6 | Формула підпису токена й підпису TUS | Токен: SHA256_HEX(security_key + video_id + expiration); TUS: SHA256_HEX(library_id + api_key + expiration + video_id) |
| R7 | Події тільки для Bunny чи для всіх джерел | Для всіх джерел (Bunny, YouTube, Vimeo) — єдиний міст і схема |
| R8 | Як вчитель бачить тривалість (енкодинг асинхронний) | Полінг `video_info` зі Studio до статусу finished; webhooks відхилено |
| R9 | Де живуть константи vs секрети | Константи — YAML під git; секрети — Django settings через Tutor |
| R10 | Мобільний WebView у MVP | Iframe-плеєр + postMessage працюють у WebView; потрібні лише CSP/Referrer-Policy |
| R11 | Domain-lock і гейт захисту відео | Token+expires у коді XBlock; Allowed domains + Block Direct URL Access — налаштування бібліотеки (ops), перевірка в quickstart |
| R12 | Статуси відео Bunny (0–6) | Фіксуємо мапування; валідація записаною фікстурою в тестах |
| R13 | Ліцензія форка | GPL-3.0 прийнятна; LICENSE збережено |
| R14 | Відкриті питання PRD («Vimeo без коду замість XBlock») | Відхилено — spec фіксує форк з трьома джерелами |

---

## R1. Форк-основа: вендоринг у репозиторій

**Decision**: скопіювати `raccoongang/xblock-video` (master) у `video_xblock/`
цього репозиторію разом з LICENSE (GPL-3.0), зберігаючи історію як
git-subtree-коміт; далі всі зміни — у нашій гілці.

**Rationale**: (1) Принцип I конституції: `scripts/trace.py` шукає маркери
`impl:`/`verifies:` саме в коді цього дерева — код фічі мусить бути тут;
(2) атомарність: зміна бекенда і його тестів — один коміт однієї гілки;
(3) відсутність залежності від зовнішнього репозиторію (build-цикл Tutor
10–20 хв не повинен ламатись чужим force-push).

**Alternatives**: (a) pip install з fork-URL у Tutor — відхилено: код поза
деревом trace.py, маркери невидимі; (b) окремий підмодуль git — відхилено:
складніше для агентів і pre-commit.

## R2. Розпізнавання бекенда: явний player_name, не url_re

**Decision**: у форці вибір бекенда робиться через `player_name`
(PlayerMixin) за збігом `url_re` з href. Для Bunny посилання не ідентифікує
відео (URL містить бібліотеку/guid і щоразу підписується наново), тому:
додаємо значення `'bunny'` у вибір `player_name`; Studio-вкладка Bunny
керує ним явно (завантаження/видалення); `url_re` бекенда — порожній
список; всі Bunny-дані — в `metadata` XBlock (через `metadata_fields()`).

**Rationale**: не ламає наявний потік YouTube/Vimeo (href-збіг), дає чіткий
стан блоку, зрозумілий при експорті/імпорті OLX.

**Alternatives**: (a) штучний href `bunny://{guid}` + url_re — відхилено:
друге джерело істини про стан, ризик розсинхрону; (b) окремий XBlock
клас — відхилено: PRD фіксує один XBlock з бекендами поряд.

## R3. Завантаження: TUS напряму з браузера

**Decision**: Studio-вкладка: `POST create_upload` (XBlock) створює об'єкт
відео в Bunny (`POST /library/{id}/videos`, header `AccessKey`) і повертає
браузеру presigned credentials `{videoId, libraryId, expirationTime,
signature}`; браузер передає файл напряму на `https://video.bunnycdn.com/tusupload`
через `tus-js-client` (headers: `AuthorizationSignature`, `AuthorizationExpire`,
`LibraryId`, `VideoId`; metadata `filetype` (MIME) і `title` обов'язкові).
Resume (FR-001-03): `tus-js-client` сам зберігає стан у localStorage і
відновлює через `findPreviousUploads()`/`resumeFromPreviousUpload()`;
якщо підпис протух — handler `upload_credentials` перепідписує той самий
GUID (Bunny це офіційно підтримує: re-sign без створення нового відео).

**Rationale**: FR-001-03 (resume), FR-001-06 (без транзиту через сервер, VPS
не вантажиться 2 ГБ), відповідає PRD «TUS-upload зі Studio»; офіційний
рекомендований шлях Bunny для файлів > 2 ГБ і нестабільних мереж.

**Alternatives**: (a) HTTP PUT `.../videos/{id}` — відхилено: без resume,
офіційно не рекомендований для великих файлів; (b) транзит через наш сервер —
відхилено: суперечить FR-001-06 і вантажить VPS; (c) `url fetch` з публічного
URL — відхилено: вчитель не має публічного URL, це інший сценарій.

## R4. Ліміти розміру / формату / тривалості

**Decision**:
- Розмір і розширення (FR-001-02, FR-001-04): перевіряються **до** створення
  upload-сесії — JS читає `file.size`/розширення і показує помилку без запиту;
  сервер у `create_upload` повторно валідує розмір проти
  `max_upload_bytes` з YAML (захист від підробки клієнта) і відхиляє 4xx.
- Тривалість (припущення spec: ≤ 30 хв): достовірна лише після енкодингу
  (Bunny повертає `length` у статусі finished) → перевірка в полінгу
  `video_info`: якщо `length > max_duration_seconds` — стан ERROR, відео
  видаляється з Bunny (DELETE), вчитель бачить повідомлення про ліміт.
  Додатково — best-effort клієнтська перевірка метаданих файла до старту
  (HTML5 video element), щоб не ганяти очевидно довгі файли.

**Rationale**: FR-001-04 вимагає відхилення до передавання — це можливо лише
для розміру/формату; тривалість фізично невідома до енкодингу, тому для неї
чесний пізній гейт із прибиранням. Всі пороги — з YAML (принцип III).

**Alternatives**: (a) не перевіряти тривалість — відхилено: порушує
припущення spec (30 хв) і контроль витрат на Bunny; (b) серверний прийом
файла заради аналізу тривалості — відхилено: суперечить FR-001-06/R3.

## R5. Плеєр: embed-плеєр Bunny + vendored player.js

**Decision**: студентське в'ю рендерить `<iframe
src="https://player.mediadelivery.net/embed/{libraryId}/{videoId}?token={...}&expires={...}">`
(+ унікальний cache-buster-параметр, `referrerpolicy="strict-origin-when-cross-origin"`).
Події отримуємо через `player.js` (postMessage), **vendored** у
`static/js/lib/playerjs.min.js` (MIT) — без CDN-залежності в рантаймі.
Події: `ready`, `play`, `pause`, `timeupdate {seconds, duration}`, `ended`,
`seeked`, `error`; методи `getDuration`, `getCurrentTime` — цього достатньо
для FR-001-12/14.

**Rationale**: офіційний плеєр Bunny: готовий HLS/адаптивний бітрейт,
працює в мобільному WebView (HTML5), token-параметри підтримуються
нативно, події стандартизовані через player.js. Найпростіше рішення, що
задовольняє вимоги (конституція: простіше за замовчуванням). YouTube/Vimeo
залишаються на наявних video.js-плеєрах форка.

**Alternatives**: (a) власний video.js + hls.js із прямими підписаними
URL — відхилено: для HLS потрібні path-style токени на кожен .ts-сегмент
(окремий режим безпеки Bunny), більше рухомих частин без виграшу;
(b) iframe без подій — відхилено: не закриває FR-001-12; (c) CDN-скрипт
player.js на льоту — відхилено: зайвий запис у CSP `script-src` і зовнішня
залежність у рантаймі (тести все одно не ходять у мережу).

## R6. Підписи: формула токена і TUS-підпису

**Decision** (за офіційною документацією Bunny Stream):
- Embed-токен: `token = SHA256_HEX(token_security_key + video_id + expiration)`,
  `expires` — UNIX-секунди; URL: `...?token=...&expires=...`. `token_security_key`
  — окремий ключ бібліотеки (Token Authentication), **не** API key.
- TUS-підпис: `signature = SHA256_HEX(library_id + api_key + expiration_time + video_id)`.
- Обидва підписи обчислюються **тільки на сервері** (ключі — секрет);
  `token_ttl_seconds` і `upload_auth_ttl_seconds` — з YAML (24 год).

**Rationale**: формули взяті з офіційних документів Bunny (Embedded view
token authentication; TUS Resumable Uploads); секрети не виходять у браузер
(401/403 від Bunny при помилці конкатенації — відомі симптоми задокументовані
в contracts/bunny-api.md).

**Alternatives**: (a) жорсткий код формул у JS — відхилено: витік ключів і
порушення принципів III/IV; (b) використання сторонньої бібліотеки підписів —
відхилено: дві однострокові sha256-функції не варті залежності.

## R7. Події перегляду: єдиний міст для всіх джерел

**Decision**: події `xblock-video.player.play|pause|complete` емітуються для
**всіх** джерел — Bunny (player.js-міст) і YouTube/Vimeo (наявний
videojs-event-plugin форка). Єдиний XBlock-хендлер `save_event` приймає
`{event_type, current_time, duration}`, доповнює серверно `{user_id,
course_id, unit_usage_key, video_ref, config_version, timestamp}` і публікує
через `runtime.publish(...)` у tracking-логи. `complete` — не більше одного
на сесію плеєра (сесія = завантаження студентського в'ю / повторний старт
після `ended`); фіксується лише коли `time/duration >= completion_threshold`
(поріг із YAML передається в контекст рендера, JS обчислює чистою функцією
під jest). Повторний повний перегляд (US3.4) = нова сесія = новий complete.

**Rationale**: FR-001-12 не обмежує джерело; метрики SC-004/005 рахуються
«по кожному юніту» — юніт з YouTube/Vimeo без подій дає хибну метрику.
Єдиний хендлер = одна схема валідації і один первинний запис (принцип V).

**Alternatives**: (a) події тільки для Bunny — відхилено: ламає метрику для
юнітів з зовнішніми джерелами; (b) окремі хендлери на джерело — відхилено:
дублювання без вигоди.

## R8. Стан готовності: полінг, не webhooks

**Decision**: Studio-вкладка після завершення TUS полінгує XBlock-хендлер
`video_info` (сервер проксіює `GET /library/{id}/videos/{videoId}`) кожні
N секунд до статусу `finished` (тоді ж береться `length` → тривалість у
вкладці) або `error/uploadFailed` (повідомлення + кнопка повтору).
Стан зберігається в `metadata` блоку.

**Rationale**: найменший рухомий механізм: жодних публічних endpoints і
перезапусків; XBlock і так знає videoId.

**Alternatives**: (a) webhooks Bunny про зміну статусу — відхилено: потрібен
публічний URL на VPS, реєстрація, секрет — складність без потреби в MVP.

## R9. Секрети vs константи

**Decision**: розділено дві категорії:
- **Поведінкові константи** (TTL 86400, поріг 0.95, ліміт 2 ГБ, 30 хв,
  розширення, endpoint-и) → `video_xblock/bunny_config.yaml` під git з
  полями `version` і `changelog` (принцип III; контракт —
  contracts/bunny-config-contract.md). `config_version` потрапляє в кожну
  подію трекінгу та в метадані відео.
- **Секрети** (Bunny API key, token security key, libraryId) → Django
  settings через tutor-plugin (env/секрети Tutor), читаються лише
  `BunnyApiClient`; ніколи не серіалізуються в course OLX і не
  передаються в браузер.

**Rationale**: конституція III вимагає версіонованого YAML для констант, що
впливають на результат; libraryId — ідентичність розгортання (не константа
поведінки), тримаємо його з секретами як інстансове налаштування.

**Alternatives**: (a) все в Django settings — відхилено: зміни поведінки без
версіонування і changelog; (b) прапорці CLI — відхилено конституцією прямо.

## R10. Мобільний WebView (етап 2) у MVP

**Decision**: нічого мобільно-специфічного не будуємо; гарантія сумісності —
iframe-плеєр Bunny (HTML5) + postMessage-події працюють у сучасному WebView.
Обов'язкові налаштування, які закладаємо вже в MVP: CSP `frame-src` для
`player.mediadelivery.net`, `referrerpolicy="strict-origin-when-cross-origin"`
на iframe (інакше domain-lock вважатиме запит «прямим» і заблокує).

**Rationale**: spec фіксує «відтворення і події працюють так само у WebView»;
техніка iframe+postMessage — нативна для обох платформ.

**Alternatives**: (a) native-плеєри SDK — відхилено: етап 2 і поза скоупом;
(b) ігнорувати Referrer-Policy — відхилено: ламає FR-001-07 під domain-lock.

## R11. Гейт захисту відео (конституція)

**Decision**: захист забезпечується двома рівнями:
1. Код XBlock (ця фіча): кожен рендер студентського в'ю підписує свіже
   посилання token+expires; у metadata ніколи немає відкритого прямого URL.
2. Ops-налаштування бібліотеки Bunny: Token Authentication увімкнено,
   Allowed domains = наш домен, Block Direct URL File Access = on.
Перевірка обох рівнів — quickstart S13 (curl без токена → 403, чужий
Referer → 403, expired → 403). Платний курс не відкривається учням, поки
S13 не пройдено — рішення приймає людина (гейт).

**Rationale**: конституційний гейт вимагає «увімкнено і перевірено»; частина
перевірки можлива тільки на живій бібліотеці, тому винесена в quickstart.

**Alternatives**: (a) лише токен без domain-lock — відхилено: гейт вимагає
обмеження домену; (b) DRM (MediaCage) — відхилено: PRD фіксує «DRM не
робимо, приймаємо ризик».

## R12. Статуси відео Bunny

**Decision**: мапування XBlock-станів на статуси Bunny (0 Created,
1 Uploaded, 2 Processing, 3 Transcoding, 4 Finished, 5 Error,
6 UploadFailed): created/uploaded → UPLOADING; processing/transcoding →
PROCESSING; finished → READY (беремо `length`); error/uploadFailed → ERROR.
Точні коди валідуються записаною фікстурою в тестах `BunnyApiClient` —
реалізація не «домислює», а читає `status` з відповіді API.

**Rationale**: коди стабільні в API Bunny, але контракт вимагає перевірки
фактом, а не пам'яттю (принцип II: фікстури).

**Alternatives**: (a) вгадувати готовність за `length != null` — відхилено:
менш надійно; (b) webhooks — див. R8.

## R13. Ліцензія форка

**Decision**: GPL-3.0 приймається як є; файл LICENSE форка зберігається в
`video_xblock/`; наші нові файли — під тією ж GPL-3.0 (похідний код).

**Rationale**: платформа внутрішня (не розповсюджується); Open edX — AGPL,
GPL-сумісність не створює конфлікту для цього проєкту.

**Alternatives**: (a) писати XBlock з нуля — відхилено: PRD фіксує форк,
переписування коштує тижні без вигоди.

## R14. Відкрите питання PRD («Vimeo замість XBlock»)

**Decision**: питання закрите spec.md — форк XBlock з трьома джерелами
(Bunny / YouTube / Vimeo); «Vimeo без коду» не розглядається.

**Rationale**: spec і PRD-таблиця «Відео» узгоджені: Bunny XBlock з
альтернативою вставки посилань YouTube/Vimeo.

---

## Поза скоупом (зафіксовано, щоб не «розповзалося»)

- Аналітичний звіт/дашборд: події цієї фічі — первинний запис; споживання —
  етап 2 PRD (FR-001-15 закривається накопиченням у tracking-логах).
- Транскрипти/субтитри Bunny, DRM, інтерактивні квізи у відео — етап 2,
  не в скоупі; наявна функціональність форка для YouTube/Vimeo не ламається.
- Мобільний застосунок — етап 2 (див. R10).
- Автентифікація/зарахування — стандартні механізми платформи (spec).

## Інтерпретації, позначені для рев'юера

- **R7**: «Плеєр MUST фіксувати події» (FR-001-12) прочитано як «для всіх
  джерел відео в юніті». Альтернативне прочитання (лише Bunny) відхилено,
  бо суперечить метрикам SC-004/005 «по кожному юніту». Якщо продуктова
  позиція інша — це зміна spec.md, не мовчазна правка плану.
- **R4**: ліміт тривалості 30 хв не має окремого FR; реалізується як
  валідація за припущеннями spec через YAML-константу.
