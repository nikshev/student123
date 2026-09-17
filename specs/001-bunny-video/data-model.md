# Phase 1 Data Model: Відео в курсах (001-bunny-video)

**Created**: 2026-09-17 | **Джерело вимог**: [spec.md](./spec.md) Key Entities,
FR-001-01..16 | **Рішення про зберігання**: [research.md](./research.md) R1/R2/R7/R9

## Принципове рішення про зберігання

- **Відео-файли** — лише у Bunny Stream (FR-001-06); на сервері файлів немає.
- **Метадані відео** — поля XBlock (серіалізуються в course OLX; у
  edx-platform це Mongo/MySQL). Нових Django-моделей **не створюється**:
  блок «Відео» — це XBlock, його стан — у `metadata` (JSONField форка).
- **Події перегляду** — tracking-логи Open edX (EVENTTRACKING → MySQL/
  Kafka/файли залежно від розгортання) через `runtime.publish`.
  Таблиці подій немає і не потрібна: FR-001-15 закривається накопиченням
  подій; звіт — споживач на етапі 2.
- **Юніт** — нативний `vertical` Open edX; нової сутності не вводимо, юніт
  адресується `usage_key` блоку (`block.scope_ids.usage_id`). Припущення
  spec «юніт містить одне відео» — обмеження контенту, не структури даних.

---

## 1. Сутність «Відео» (блок «Відео» XBlock `video_xblock`)

| Поле | Тип | Джерело | Опис / правила |
|---|---|---|---|
| `display_name` | String | XBlock field | Назва блоку в Studio |
| `player_name` | Enum `{youtube, vimeo, brightcove, html5, wistia, tencent, bunny}` | XBlock field (`PlayerMixin`) | Явний вибір джерела; `bunny` виставляється тільки вкладкою Bunny (R2) |
| `href` | String | XBlock field | Для YouTube/Vimeo — посилання (наявний потік); для Bunny — порожнє (відео ідентифікується метаданими) |
| `metadata` | JSON (JSONField форка) | XBlock field | Тільки бекенд-специфічні ключі; для Bunny — схема нижче. Секретів не містить ніколи (R9) |

### `metadata` для `player_name='bunny'` (склад — `metadata_fields()` бекенда)

| Ключ | Тип | Опис |
|---|---|---|
| `bunny_video_id` | String (GUID) | Ідентифікатор відео в Bunny (з `Create Video`) |
| `bunny_library_id` | Integer | ID бібліотеки (для валідації відповідей) |
| `bunny_status` | Enum | Див. стани нижче |
| `bunny_length_seconds` | Float\|null | Тривалість із `length` відповіді Bunny (доступна в READY) |
| `bunny_title` | String | Назва, передана в Bunny при створенні |
| `source_type` | `"bunny"` | Нев'язка для подій/майбутньої аналітики |
| `token_protected` | Boolean | `true` для bunny (виводиться для подій/гейту) |
| `config_version` | String | Версія `bunny_config.yaml` на момент запису (принцип III) |
| `upload_signature_expires` | Integer\|null | UNIX-секунди протухання останнього TUS-підпису (для resume-логіки UI) |

**Інваріанти**:
1. `bunny_video_id` присутній ⟺ `bunny_status ∈ {UPLOADING, PROCESSING, READY, ERROR}`.
2. Секрети (API key, token key) у полях блоку **відсутні** — будь-який експорт
   OLX безпечний.
3. `bunny_status=READY` ⟹ `bunny_length_seconds` заповнено і
   `bunny_length_seconds ≤ max_duration_seconds` (R4).
4. URL студентського плеєра ніколи не зберігається — підписується на рендер
   (FR-001-09).

### Стани відео та переходи

| Стан XBlock | Відповідність статусу Bunny | У Studio показує |
|---|---|---|
| `EMPTY` | (об'єкта нема) | Кнопки «Завантажити» / «Вставити посилання» |
| `UPLOADING` | 0 Created, 1 Uploaded | Прогрес завантаження (TUS) |
| `PROCESSING` | 2 Processing, 3 Transcoding | «Обробляється…» + полінг |
| `READY` | 4 Finished | Тривалість, стан «готово» (FR-001-05) |
| `ERROR` | 5 Error, 6 UploadFailed або валідація R4 | Повідомлення про помилку, «Повторити»/«Видалити» |

```
EMPTY ──create_upload──▶ UPLOADING ──TUS done──▶ PROCESSING ──poll: finished──▶ READY
  ▲                         │  ▲                    │                                │
  │                         │  └──resume (той самий GUID)                           │
  │                         ▼                      ▼                                ▼
  └─────delete_video──── EMPTY ◀──delete_video──── ERROR ◀──poll: error/ліміт──── READY ──replace──▶ UPLOADING (новий GUID, старий DELETE)
```

Правила:
- Перехід у `ERROR` з будь-якого стану — через результат полінгу або
  валідацію тривалості; при цьому відео в Bunny видаляється (не лишаємо
  сиріт, FR-001-16/«видалене відео»).
- `replace` (FR-001-16): новий `create_upload` поверх наявного → старий
  `bunny_video_id` видаляється з Bunny, метадані перезаписуються. Події
  перегляду інших юнітів не чіпаються (події адресують свій `usage_key`).
- Resume після обриву (FR-001-03): той самий GUID; якщо TUS-ресурс протух
  (≈48 год простою або за `AuthorizationExpire`) — почати заново (новий GUID,
  старий DELETE).

---

## 2. Сутність «Подія перегляду» (tracking event)

Первинний запис метрики «додивились до кінця» (принцип V). Публікується
XBlock-хендлером `save_event` у tracking-лог Open edX. Схема — контракт
[contracts/tracking-events.md](./contracts/tracking-events.md).

| Поле | Тип | Джерело | Опис |
|---|---|---|---|
| `user_id` | Integer | Сервер (request.user) | Хто дивився (FR-001-13) |
| `course_id` | String | Сервер (context) | Курс |
| `unit_usage_key` | String | Сервер (`scope_ids.usage_id`) | Юніт (FR-001-13) |
| `video_ref` | Object | Сервер (метадані) | `{source_type, video_id}` — bunny GUID або URL YouTube/Vimeo |
| `event_type` | Enum `{play, pause, complete}` | Клієнт | FR-001-12 |
| `current_time` | Float | Клієнт | Позиція на момент події (сек) |
| `duration` | Float\|null | Клієнт/сервер | Тривалість (для Bunny — з метаданих, не від клієнта) |
| `config_version` | String | Сервер | Версія YAML-констант (принцип III) |
| `timestamp` | ISO-8601 | Tracking-система | Мітка часу (FR-001-13) |

**Правила фіксації**:
- `play` — на подію `play` плеєра (у т.ч. продовження після паузи; пара
  play↔pause дозволяє порахувати сумарний час перегляду).
- `pause` — на подію `pause`; якщо плеєр закрито без події паузи (закриття
  вкладки), фіксація паузи не гарантується — прийнятно, бо «неповний
  перегляд» виводиться з відсутності `complete` (US3.2).
- `complete` — не більше одного на сесію плеєра; умова:
  `time / duration ≥ completion_threshold` (YAML, за замовчуванням 0.95,
  FR-001-14). Перемотка останніх секунд (edge case spec) сама собою не дає
  `complete` — грає поріг. Повторний повний перегляд (US3.4) — нова сесія,
  новий `complete`; обидва зберігаються окремо.
- Сесія = одне завантаження студентського в'ю; перезапуск відтворення після
  `ended` у тому ж в'ю відкриває нову сесію (прапорець скидається).
- `duration` для Bunny береться з метаданих блоку (недовіряємо клієнту);
  для YouTube/Vimeo — з плеєра (video.js).

---

## 3. Сутність «Юніт»

Не моделюється окремо: нативний `vertical` платформи, ідентифікується
`usage_key` блоку «Відео». Вимоги FR-001-13/15 адресують саме його.
Обмеження «одне відео на юніт» — інструкція контенту (spec), не constraint
БД: кілька блоків на юніт не ламають модель, але не підтримуються сценаріями.

---

## 4. Артефакт конфігурації `bunny_config.yaml` (принцип III)

Версіонований YAML у `video_xblock/bunny_config.yaml`. Повний контракт —
[contracts/bunny-config-contract.md](./contracts/bunny-config-contract.md).

| Ключ | Значення за замовчуванням | Вплив |
|---|---|---|
| `version` | `1.0.0` | Ідентичність прогону; потрапляє в події/метадані |
| `changelog` | список `{version, date, changes}` | Обов'язковий при зміні (конституція III) |
| `token_ttl_seconds` | `86400` | Термін дії токена плеєра (FR-001-09, SC-003) |
| `completion_threshold` | `0.95` | Поріг повного перегляду (FR-001-14) |
| `max_upload_bytes` | `2147483648` | Ліміт 2 ГБ (FR-001-04) |
| `max_duration_seconds` | `1800` | Ліміт 30 хв (припущення spec, R4) |
| `allowed_extensions` | `[mp4, mov, webm]` | FR-001-02 |
| `upload_auth_ttl_seconds` | `86400` | Термін дії TUS-підпису |
| `api_base_url` | `https://video.bunnycdn.com` | REST Bunny |
| `tus_endpoint` | `https://video.bunnycdn.com/tusupload` | TUS |
| `embed_base_url` | `https://player.mediadelivery.net` | Плеєр |
| `video_info_poll_interval_seconds` | `5` | Полінг стану в Studio (R8) |

Валідація при завантаженні: типи і межі (0 < threshold ≤ 1, ttl > 0,
extensions — непорожній список), інакше XBlock не стартує з явною помилкою.
Зміна будь-якого поведінкового ключа без запису в `changelog` заборонена.
