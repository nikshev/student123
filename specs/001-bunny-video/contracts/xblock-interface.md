# Contract: бекенд Bunny у XBlock (розширення форка) та JSON-хендлери

**Feature**: 001-bunny-video | **База**: `raccoongang/xblock-video`,
`video_xblock/backends/base.py` (`BaseVideoPlayer`, `BaseApiClient`)

## 1. Новий бекенд `video_xblock/backends/bunny.py`

`BunnyPlayer(BaseVideoPlayer)` + `BunnyApiClient(BaseApiClient)`.
Це єдиний модуль із знанням про Bunny (принцип IV).

### `BunnyPlayer` — точки розширення форка, які ми реалізуємо

| Точка | Значення / поведінка |
|---|---|
| `entry_point` | `video_xblock.v1` (реєстрація поряд з youtube/vimeo) |
| `url_re` | `[]` — Bunny не розпізнається за href (R2) |
| `metadata_fields()` | `['bunny_video_id', 'bunny_library_id', 'bunny_status', 'bunny_length_seconds', 'bunny_title', 'source_type', 'token_protected', 'config_version', 'upload_signature_expires']` (data-model §1) |
| `basic_fields` | `['display_name']` (без `href` — посилання не вводиться) |
| `advanced_fields` | `['start_time', 'end_time', 'handout', 'download_transcript_allowed']` — без `download_video_url` (прямого URL не існує) |
| `validate_data` | Валідація метаданих Bunny (наявність GUID у непорожньому стані, числовий `bunny_length_seconds`) |
| `media_id(href)` | `''` (не застосовний) |
| `get_player_html(**context)` | Перевизначено: рендер `bunny_student_view.html` — iframe embed з підписаним URL + `static/js/lib/playerjs.min.js` + `bunny_player.js` |
| `player_data_setup(context)` | Розширення контексту: `bunny_config` (з YAML: `completion_threshold`, `token_ttl_seconds`), `bunny_video_id`, `signed_embed_url` (обчислений сервером), `config_version` |

Студентський рендер не робить синхронних викликів Bunny: підписане
посилання формується сервером під час рендера, виключно для зарахованого
учня (авторизацію виконує платформа, FR-001-08).

### `BunnyApiClient` — обов'язкові методи

| Метод | Виклик | Куди |
|---|---|---|
| `create_video(title)` | POST §1 bunny-api | `create_upload` |
| `sign_upload(video_id)` | локальне sha256, TUS §2 | `create_upload`, `upload_credentials` |
| `get_video_info(video_id)` | GET §3 | `video_info` |
| `delete_video(video_id)` | DELETE §4 | `delete_video`, replace, ліміт тривалості |
| `signed_embed_url(video_id)` | локальне sha256, §5 | студентський рендер |
| `healthcheck()` | GET `.../library/{id}/videos?perPage=1` | діагностика Studio («Підключення до Bunny: OK/помилка») |

Усі HTTP-виклики читають `api_base_url`/`tus_endpoint`/`embed_base_url` з
YAML і секрети з Django settings. Помилки API перекладаються у зрозумілі
повідомлення (не сирі тексти Bunny).

## 2. JSON-хендлери XBlock

Хендлери оголошуються в `BunnyPlayer`/`VideoXBlock` і доступні лише з
відповідних в'ю (Studio — автор курсу; LMS — зарахований учень).
Схеми — контракт між JS і Python; валідація входу — на сервері.

### 2.1 `create_upload` (Studio, `POST /handler/create_upload`)

Запит: `{"file_name": "лекція1.mp4", "file_size": 104857600, "file_type": "video/mp4"}`
- Перевірки до будь-якого виклику Bunny (FR-001-02/04):
  розширення ∈ `allowed_extensions`, `file_size ≤ max_upload_bytes`,
  MIME починається з `video/` (усе з YAML).
- Створює об'єкт відео в Bunny і повертає:

```json
{
  "video_id": "32d140e2-...",
  "library_id": 759,
  "tus_endpoint": "https://video.bunnycdn.com/tusupload",
  "authorization_signature": "hex...",
  "authorization_expire": 1750000000
}
```

Помилки: `400` (формат/розмір, з текстом ліміту), `502` (Bunny недоступний).
Після успіху блок переходить у `UPLOADING`.

### 2.2 `upload_credentials` (Studio, `POST /handler/upload_credentials`)

Запит: `{"video_id": "32d140e2-..."}` — повторний підпис того самого GUID
для resume (R3). Відповідь — як у 2.1 (без створення нового відео).
`404` з TUS під час resume → UI починає заново через `create_upload`.

### 2.3 `video_info` (Studio, `POST /handler/video_info`)

Запит: `{"video_id": "32d140e2-..."}`
Відповідь: `{"status": 4, "length": 612.5}` (мапування статусів — bunny-api §3;
`length` — лише для status 4). Полінг з інтервалом
`video_info_poll_interval_seconds`; статус і тривалість зберігаються в
`metadata` (FR-001-05).

### 2.4 `delete_video` (Studio, `POST /handler/delete_video`)

Запит: `{}` (бере `bunny_video_id` з метаданих блоку).
Видаляє відео в Bunny і повертає стан блоку в `EMPTY`. FR-001-16.
Replace = `create_upload` після `delete_video`.

### 2.5 `save_event` (LMS, `POST /handler/save_event`)

Запит (від JS-моста):

```json
{"event_type": "play", "current_time": 12.0, "duration": 612.5}
```

`event_type ∈ {play, pause, complete}`; сервер ігнорує `duration` для Bunny
(бере з метаданих), підставляє `user_id`/`course_id`/`unit_usage_key`/
`video_ref`/`config_version` і публікує подію — див. tracking-events.md.
`complete` приймається лише раз на сесію (серверний прапорець на сесію не
тримаємо — дедуплікація клієнтська; сервер валідує тип і числа).

## 3. JS-компоненти (тонкі, логіка — у чистих функціях)

- `bunny_upload.js` (Studio): `tus-js-client`; віддає пресет-данні з 2.1;
  resume через `findPreviousUploads`; прогрес-бар; обробка помилок TUS.
- `bunny_player.js` (LMS): міст player.js → `save_event`; чиста функція
  `shouldFireComplete(current, duration, threshold)` (jest); прапорець
  сесійної дедуплікації; повідомлення про помилку відтворення (FR-001-10,
  edge «немає мережі»).
- Події для YouTube/Vimeo йдуть наявним videojs-event-plugin форка на той
  самий `save_event` (R7).

## 4. Розширення налаштувань форка

- `player_name`: додається `'bunny'` до існуючого вибору.
- Налаштування бекенда з Django settings: `BUNNY_STREAM_LIBRARY_ID`,
  `BUNNY_STREAM_API_KEY`, `BUNNY_STREAM_TOKEN_KEY` (секрети, R9); шлях до
  `bunny_config.yaml` — всередині пакета (не налаштовується ззовні).
