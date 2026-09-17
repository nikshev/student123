# Contract: XBlock ↔ Bunny Stream API

**Feature**: 001-bunny-video | **Status**: затверджено на фазі plan | **Source of truth**: офіційна документація Bunny Stream (2026)

Цей контракт описує **єдину** поверхню розмови XBlock із Bunny Stream.
Будь-який код поза `video_xblock/backends/bunny.py` не має права викликати
ці endpoints напряму (принцип IV). Усі виклики проходять через
`BunnyApiClient` (субклас `BaseApiClient` форка: `get`/`post`/`delete`),
отже тестуються записаними фікстурами без мережі (принцип II).

Базові URL з `bunny_config.yaml`: `api_base_url`, `tus_endpoint`,
`embed_base_url`. Секрети (API key, token security key, libraryId) — з
Django settings через tutor-plugin; у браузер не передаються (R9).

## 1. Створення об'єкта відео

```
POST {api_base_url}/library/{libraryId}/videos
Headers:
  AccessKey: {library_api_key}
  Accept: application/json
  Content-Type: application/json
Body: {"title": "{bunny_title}"}
```

Відповідь 200 (використовувані поля):

```json
{
  "guid": "32d140e2-e4f4-4eec-9d53-20371e9be607",
  "videoLibraryId": 759,
  "title": "Лекція 1",
  "dateUploaded": "2026-09-17T12:00:00.000Z"
}
```

- `guid` → `metadata.bunny_video_id`.
- Викликається тільки з XBlock-хендлера `create_upload` (Studio, автор).
- Помилки: `401` (невірний AccessKey/бібліотека) → ERROR, повідомлення
  «Помилка сервісу відео, спробуйте пізніше».

## 2. TUS-завантаження (resumable)

```
POST/HEAD/PATCH {tus_endpoint}            # https://video.bunnycdn.com/tusupload
Headers (кожен запит):
  AuthorizationSignature: sha256_hex("{libraryId}{api_key}{expirationTime}{videoId}")
  AuthorizationExpire:   {unix_seconds}   # now + upload_auth_ttl_seconds
  LibraryId:             {libraryId}
  VideoId:               {guid}
TUS metadata: filetype={mime}, title={name}   # обидва обов'язкові
```

- Підпис обчислює **сервер** у `create_upload`/`upload_credentials`; браузер
  отримує готові `{videoId, libraryId, expirationTime, signature}` і
  передає файл напряму (`tus-js-client`).
- Resume: `tus-js-client` відновлює з `Upload-Offset` (стан у localStorage);
  дозволено перепідписати той самий GUID без створення нового об'єкта.
- Термін життя неповного завантаження: до `AuthorizationExpire` або ≈48 год
  простою; після — `404` (не `410`). На `404` під час resume XBlock
  починає заново з новим GUID (старий — DELETE).
- `AuthorizationExpire` перевіряється на кожному POST/HEAD/PATCH, але не
  перериває активний PATCH-потік (edge case «сплив під час перегляду» —
  аналогія для завантаження).

## 3. Інформація про відео (полінг стану, R8)

```
GET {api_base_url}/library/{libraryId}/videos/{videoId}
Headers: AccessKey: {library_api_key}, Accept: application/json
```

Використовувані поля відповіді:

```json
{
  "guid": "32d140e2-...",
  "status": 4,
  "length": 612.5,
  "title": "Лекція 1"
}
```

Мапування `status` (валідується фікстурою в тестах):

| status | Значення | Стан XBlock |
|---|---|---|
| 0 | Created | UPLOADING |
| 1 | Uploaded | UPLOADING |
| 2 | Processing | PROCESSING |
| 3 | Transcoding | PROCESSING |
| 4 | Finished | READY (`length` → `bunny_length_seconds`) |
| 5 | Error | ERROR |
| 6 | UploadFailed | ERROR |

- Після `4`: перевірка `length ≤ max_duration_seconds`; перевищення →
  ERROR + DELETE (R4).
- Помилки: `404` (видалено в Bunny) → ERROR з повідомленням учню «Відео
  недоступне» (FR-001-10).

## 4. Видалення відео

```
DELETE {api_base_url}/library/{libraryId}/videos/{videoId}
Headers: AccessKey: {library_api_key}, Accept: application/json
```

Відповідь 200 `{"success": true, "message": "OK"}`.
Викликається з `delete_video` і при заміні/помилці ліміту. Повторний DELETE
існуючого — не фатальний (ідемпотентна обробка помилки `404` як успіху).

## 5. Посилання плеєра (підписане)

```
GET {embed_base_url}/embed/{libraryId}/{videoId}?token={token}&expires={expires}
```

Формула (сервер, тільки `BunnyApiClient`):

```
token   = sha256_hex("{token_security_key}{videoId}{expires}")   # HEX, рядкова конкатенація
expires = unix_seconds(now + token_ttl_seconds)                  # секунди, не ms
```

- `token_security_key` — ключ Token Authentication бібліотеки, **не** API key.
- Посилання генерується на кожен рендер студентського в'ю, ніколи не
  зберігається в метаданих; `expires` визначається `token_ttl_seconds`
  (86400 → SC-003).
- Унікальність iframe для кількох плеєрів на сторінці: додається
  cache-buster-параметр (вимога player.js).
- `403` від embed ⟹ невірний/протухлий токен, відсутні параметри, або
  Referer поза Allowed domains — мапується на UI «Відео недоступне».

## 6. Фікстури (обов'язково для тестів)

Кожен endpoint має записані фікстури в `video_xblock/tests/fixtures/bunny/`:
успішні відповіді, `401/403/404`, статуси 0–6, відповідь із `length`.
Тести `BunnyApiClient` і хендлерів використовують лише їх (без мережі).

## 7. Обмеження доступу

- `create_upload`, `upload_credentials`, `video_info`, `delete_video` —
  тільки Studio (перевірка `is_studio`/авторизації автора).
- `save_event` — тільки LMS, зарахування перевіряє платформа.
- API key і token security key ніколи не з'являються в браузері, логах
  рендера чи course OLX.
