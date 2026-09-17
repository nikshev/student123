# Contract: версіонований YAML констант (`video_xblock/bunny_config.yaml`)

**Feature**: 001-bunny-video | **Принцип**: конституція III (константи, що
впливають на результат, — у YAML під git із версією; зміна без changelog
заборонена).

## 1. Розташування і завантаження

- Файл: `video_xblock/bunny_config.yaml` (пакується з XBlock, змінюється
  лише комітом).
- Завантажується один раз на старті XBlock із жорсткою валідацією схеми:
  некоректний YAML/типи/межі → XBlock не стартує з явною помилкою
  (fail-fast замість «тихих» значень за замовчуванням).
- `version` конфігурації штампується в: кожну подію трекінгу
  (`config_version`), метадані відео (`metadata.config_version`), контекст
  студентського в'ю. Два прогони з різними версіями артефакту не
  порівнюються — і це видно з метаданих без читання коду.

## 2. Приклад (канонічний, `version: 1.0.0`)

```yaml
# video_xblock/bunny_config.yaml
# Поведінкові константи XBlock «Відео» (Bunny Stream).
# Зміна будь-якого значення ВИМАГАЄ запису в changelog і підйому version.

version: 1.0.0

token_ttl_seconds: 86400            # FR-001-09, SC-003: термін дії токена плеєра
completion_threshold: 0.95          # FR-001-14: поріг повного перегляду
max_upload_bytes: 2147483648        # FR-001-04: ліміт розміру (2 ГБ)
max_duration_seconds: 1800          # припущення spec: максимальна тривалість (30 хв)
allowed_extensions: [mp4, mov, webm]  # FR-001-02
upload_auth_ttl_seconds: 86400      # термін дії TUS-підпису завантаження
video_info_poll_interval_seconds: 5 # полінг стану енкодингу в Studio

api_base_url: https://video.bunnycdn.com
tus_endpoint: https://video.bunnycdn.com/tusupload
embed_base_url: https://player.mediadelivery.net

changelog:
  - version: 1.0.0
    date: 2026-09-17
    changes:
      - "Початкові значення: TTL 24 год (spec Assumptions), поріг 95 % (FR-001-14), 2 ГБ (spec), 30 хв (spec)"
```

## 3. Схема і межі валідації

| Ключ | Тип | Межі | Потрапляє в |
|---|---|---|---|
| `version` | semver-строка | обов'язковий, змінюється разом із changelog | події, метадані, контекст JS |
| `token_ttl_seconds` | int | 1…2592000 | підпис embed URL |
| `completion_threshold` | float | 0.0 < x ≤ 1.0 | контекст JS (jest-логіка) |
| `max_upload_bytes` | int | > 0 | валідація `create_upload` |
| `max_duration_seconds` | int | > 0 | валідація після енкодингу |
| `allowed_extensions` | list[str] | непорожній, `[a-z0-9]+` | валідація `create_upload` |
| `upload_auth_ttl_seconds` | int | ≥ 3600 (рекомендація Bunny) | TUS-підпис |
| `video_info_poll_interval_seconds` | int | 1…60 | полінг Studio |
| `api_base_url` / `tus_endpoint` / `embed_base_url` | str (https URL) | обов'язкові | `BunnyApiClient` |
| `changelog` | list `{version, date, changes[]}` | не зменшується | аудит |

Секрети в цей файл не кладуться: API key / token security key / libraryId —
Django settings через tutor-plugin (R9). LibraryId не є поведінковою
константою — ідентичність розгортання.

## 4. Процедура зміни (конституція III)

1. Підняти `version` (semver; major — зміна, що змінює висновки подій, напр.
   поріг; minor/patch — інше).
2. Додати запис у `changelog` з причиною.
3. Окремий коміт, який міняє лише цей файл (+ тести, якщо межі залежать).
4. Рев'юер перевіряє: зміна значення без changelog — `CHANGES_REQUESTED`.
