# Матриця трасування

<!-- ГЕНЕРУЄТЬСЯ scripts/trace.py — не редагувати вручну -->

Вимог: **30** · задач: **93** (виконано 61) · вимог у роботі: **14** · порушень: **0**

## 001-bunny-video

| Вимога | Опис | Задачі | Імплементація | Тести |
|---|---|---|---|---|
| `FR-001-01` | Вчитель-автор MUST мати змогу завантажити відеофайл у юніт курсу через редактор (Studio) без сторонніх інструм | `T-001`, `T-002`, `T-016`, `T-017`, `T-038` | `video_xblock/setup.py`, `video_xblock/video_xblock/constants.py`, `video_xblock/video_xblock/utils.py`, `video_xblock/video_xblock/video_xblock.py`, `video_xblock/py312-stubs/setup.py`, `video_xblock/video_xblock/backends/bunny.py`, `video_xblock/video_xblock/backends/base.py`, `video_xblock/py312-stubs/py312_compat/__init__.py`, `video_xblock/py312-stubs/openedx/core/djangoapps/site_configuration/helpers.py`, `video_xblock/py312-stubs/openedx/core/djangoapps/contentserver/middleware.py`, `video_xblock/py312-stubs/common/djangoapps/util/date_utils.py`, `video_xblock/py312-stubs/xmodule/contentstore/django.py`, `video_xblock/py312-stubs/xmodule/contentstore/content.py` | `video_xblock/video_xblock/tests/unit/test_backends.py`, `video_xblock/video_xblock/tests/unit/test_bunny_studio_view.py`, `video_xblock/video_xblock/tests/unit/test_mixins.py` |
| `FR-001-02` | Система MUST приймати відеофайли формату MP4, MOV і WebM; для непідтримуваного файла вчитель MUST бачити зрозу | `T-004`, `T-005`, `T-008`, `T-009`, `T-018`, `T-019` | `video_xblock/video_xblock/bunny_config.yaml`, `video_xblock/video_xblock/bunny_config.py`, `video_xblock/video_xblock/backends/bunny.py` | `video_xblock/video_xblock/tests/unit/test_bunny_config.py`, `video_xblock/video_xblock/tests/unit/test_create_upload.py` |
| `FR-001-03` | Завантаження MUST бути стійким до обриву мережі: повторна спроба того самого файла продовжується з місця обрив | `T-012`, `T-013`, `T-018`, `T-019` | `video_xblock/video_xblock/backends/bunny.py` | `video_xblock/video_xblock/tests/unit/test_upload_credentials.py` |
| `FR-001-04` | Система MUST відхиляти файли понад встановлений ліміт розміру до початку передавання з поясненням ліміту. | `T-004`, `T-005`, `T-010`, `T-011`, `T-018`, `T-019` | `video_xblock/video_xblock/bunny_config.yaml`, `video_xblock/video_xblock/bunny_config.py`, `video_xblock/video_xblock/backends/bunny.py` | `video_xblock/video_xblock/tests/unit/test_bunny_config.py`, `video_xblock/video_xblock/tests/unit/test_upload_limits.py` |
| `FR-001-05` | Після завершення завантаження відео MUST бути доступним для публікації в юніті без додаткових дій вчителя, і в | `T-006`, `T-007`, `T-014`, `T-015` | `video_xblock/video_xblock/backends/bunny.py` | `video_xblock/video_xblock/tests/unit/test_video_info.py`, `video_xblock/video_xblock/tests/unit/test_bunny_api_client.py` |
| `FR-001-06` | Відео MUST зберігатися поза сервером платформи; на сервері лишаються лише метадані та посилання. | `T-020`, `T-021` | `video_xblock/video_xblock/video_xblock.py`, `video_xblock/video_xblock/backends/bunny.py` | `video_xblock/video_xblock/tests/unit/test_bunny_metadata.py` |
| `FR-001-07` | Учень, зарахований на курс, MUST мати змогу відтворити відео в плеєрі юніту без переходу на сторонні сайти. | `T-003`, `T-026`, `T-027` | `tutor-plugin/plugin.yml`, `video_xblock/video_xblock/backends/bunny.py` | `video_xblock/video_xblock/tests/unit/test_student_view.py` |
| `FR-001-08` | Учень, не зарахований на курс, MUST NOT мати доступу до відео юніту. | `T-028`, `T-029` | `video_xblock/video_xblock/backends/bunny.py` | `video_xblock/video_xblock/tests/unit/test_access_guards.py` |
| `FR-001-09` | Посилання на захищене відео MUST бути підписане токеном з обмеженим терміном дії; після спливу терміну доступ  | `T-004`, `T-005`, `T-006`, `T-007`, `T-012`, `T-013`, `T-024`, `T-025` | `video_xblock/video_xblock/bunny_config.yaml`, `video_xblock/video_xblock/bunny_config.py`, `video_xblock/video_xblock/backends/bunny.py` | `video_xblock/video_xblock/tests/unit/test_bunny_config.py`, `video_xblock/video_xblock/tests/unit/test_upload_credentials.py`, `video_xblock/video_xblock/tests/unit/test_bunny_api_client.py`, `video_xblock/video_xblock/tests/unit/test_student_view.py`, `video_xblock/video_xblock/tests/unit/test_bunny_api_client_signing.py` |
| `FR-001-10` | Якщо відео недоступне (видалене, помилка відтворення), учень MUST бачити зрозуміле повідомлення про недоступні | `T-030`, `T-031` | `video_xblock/video_xblock/backends/bunny.py` | `video_xblock/video_xblock/tests/unit/test_unavailable.py` |
| `FR-001-11` | Вчитель MUST мати змогу вставити відео за готовим посиланням YouTube або Vimeo; таке відео відтворюється в пле | `T-036`, `T-037`, `T-039` | `video_xblock/video_xblock/backends/bunny.py` | `video_xblock/video_xblock/tests/unit/test_youtube_vimeo.py` |
| `FR-001-12` | Плеєр MUST фіксувати події перегляду: початок відтворення, пауза/продовження, повний перегляд. | `T-034`, `T-035` | `video_xblock/video_xblock/backends/bunny.py` | `video_xblock/video_xblock/tests/unit/test_save_event.py` |
| `FR-001-13` | Кожна подія перегляду MUST бути прив'язана до конкретного учня, юніту і мітки часу. | `T-034`, `T-035` | `video_xblock/video_xblock/backends/bunny.py` | `video_xblock/video_xblock/tests/unit/test_save_event.py` |
| `FR-001-14` | Повний перегляд MUST зараховуватися лише якщо відтворено щонайменше 95 % тривалості відео, а не лише його кіне | `T-004`, `T-005`, `T-024`, `T-032`, `T-033` | `video_xblock/video_xblock/bunny_config.yaml`, `video_xblock/video_xblock/bunny_config.py`, `video_xblock/video_xblock/backends/bunny.py` | `video_xblock/video_xblock/tests/unit/test_bunny_config.py`, `video_xblock/video_xblock/tests/unit/test_student_view.py` |
| `FR-001-15` | Події перегляду MUST накопичуватися так, щоб частка повних переглядів по кожному юніту була доступна в звіті н | `T-034`, `T-035` | `video_xblock/video_xblock/backends/bunny.py` | `video_xblock/video_xblock/tests/unit/test_save_event.py` |
| `FR-001-16` | Вчитель MUST мати змогу видалити або замінити відео в юніті; це не повинно впливати на події перегляду інших ю | `T-022`, `T-023` | `video_xblock/video_xblock/backends/bunny.py` | `video_xblock/video_xblock/tests/unit/test_delete_video.py` |

## 002-ai-tutor

| Вимога | Опис | Задачі | Імплементація | Тести |
|---|---|---|---|---|
| `FR-002-01` | Учень, зарахований на курс, MUST мати змогу поставити питання репетитору в інтерфейсі юніту і отримати відпові | `T-001`, `T-002`, `T-005`, `T-006`, `T-019`, `T-020`, `T-027`, `T-028` | `ai_tutor_test_settings.py`, `ai_tutor_xblock/__init__.py`, `ai_tutor_service/settings_test.py`, `ai_tutor_service/__init__.py`, `ai_tutor_xblock/tests/conftest.py`, `ai_tutor_service/tutoring/prompting.py`, `ai_tutor_service/tutoring/__init__.py`, `ai_tutor_service/tutoring/pipeline.py`, `ai_tutor_service/api/ask.py`, `ai_tutor_service/tests/conftest.py` | `ai_tutor_xblock/tests/test_xblock_harness.py`, `tests/architecture/test_ai_tutor_layout.py`, `ai_tutor_service/tests/test_service_harness.py`, `ai_tutor_service/tests/integration/test_ask_api.py` |
| `FR-002-02` | Учень, не зарахований на курс, MUST NOT мати доступу до репетитора. | `T-015`, `T-016`, `T-023`, `T-024` | `ai_tutor_xblock/ai_tutor_xblock/block.py`, `ai_tutor_xblock/ai_tutor_xblock/guards.py`, `ai_tutor_service/api/apps.py`, `ai_tutor_service/api/urls.py`, `ai_tutor_service/api/errors.py`, `ai_tutor_service/api/auth.py`, `ai_tutor_service/api/__init__.py` | `ai_tutor_xblock/tests/unit/test_access_guards.py`, `ai_tutor_service/tests/contract/test_api_auth.py` |
| `FR-002-03` | Відповідь репетитора MUST спиратися на матеріали поточного юніту (транскрипт лекції, конспект); де можливо, ві | `T-017`, `T-018`, `T-029`, `T-030`, `T-031`, `T-032` | `ai_tutor_service/api/views.py`, `ai_tutor_service/materials/retriever.py`, `ai_tutor_service/materials/repository.py` | `ai_tutor_service/tests/integration/test_materials_api.py`, `ai_tutor_service/tests/unit/test_fts_retriever.py` |
| `FR-002-04` | Якщо в матеріалах юніту немає відповіді, репетитор MUST чесно повідомити про це, а не вигадувати відповідь. | `T-033`, `T-034` | — | — |
| `FR-002-05` | Репетитор MUST NOT давати готових розв'язань навчальних завдань; він MUST пояснювати підхід до розв'язання. | `T-035`, `T-036` | — | — |
| `FR-002-06` | Відповідь із готовим розв'язанням навчального завдання MUST блокуватися автоматично до показу учневі. | `T-037`, `T-038` | — | — |
| `FR-002-07` | Кожен факт блокування MUST бути записаний у лог: учень, курс, юніт, питання, час. | `T-039`, `T-040` | — | — |
| `FR-002-08` | Після блокування учень MUST бачити зрозуміле пояснення правила («допомагаю розібратися, а не розв'язую за тебе | `T-041`, `T-042` | — | — |
| `FR-002-09` | Кожен запит до репетитора MUST фіксуватися в подіях як первинний запис: учень, курс, юніт, час, тема питання — | `T-045`, `T-046`, `T-047`, `T-048` | — | — |
| `FR-002-10` | Відповідь MUST надходити протягом 30 секунд у звичайних умовах; якщо сервіс тимчасово недоступний, учень MUST  | `T-003`, `T-004`, `T-011`, `T-012`, `T-013`, `T-014`, `T-025`, `T-026` | `tutor-plugin/plugin.yml`, `ai_tutor_xblock/ai_tutor_xblock/client.py`, `ai_tutor_xblock/ai_tutor_xblock/__init__.py`, `ai_tutor_service/providers/client.py`, `ai_tutor_service/providers/__init__.py` | `ai_tutor_xblock/tests/contract/test_tutor_service_client.py`, `tests/architecture/test_ai_tutor_tutor_plugin.py`, `ai_tutor_service/tests/contract/test_llm_client.py` |
| `FR-002-11` | Кількість запитів на учня MUST бути обмежена добовим лімітом; при досягненні ліміту учень MUST бачити поясненн | `T-007`, `T-008`, `T-021`, `T-022` | `ai_tutor_service/tutor_config.yaml`, `ai_tutor_service/config.py`, `ai_tutor_service/limits/service.py` | `ai_tutor_service/tests/unit/test_daily_quota.py`, `ai_tutor_service/tests/unit/test_tutor_config.py` |
| `FR-002-12` | Учень MUST бачити історію свого діалогу з репетитором у межах юніту. | `T-049`, `T-050` | — | — |
| `FR-002-13` | Перед допуском репетитора до живих учнів MUST прогонятися контрольна вибірка завдань: частка відповідей із гот | `T-043`, `T-044`, `T-053`, `T-054` | — | — |
| `FR-002-14` | Історія діалогів MUST зберігати мінімум даних, потрібних для метрик і гейт-перевірок; доступ до неї MUST мати  | `T-009`, `T-010`, `T-051`, `T-052` | `ai_tutor_service/conversations/models.py`, `ai_tutor_service/limits/models.py`, `ai_tutor_service/guard/models.py`, `ai_tutor_service/providers/models.py`, `ai_tutor_service/materials/models.py` | `ai_tutor_service/tests/unit/test_data_models.py` |

## Критичні задачі

- `T-007` (001-bunny-video) — виконано
- `T-013` (001-bunny-video) — виконано
- `T-015` (001-bunny-video) — виконано
- `T-025` (001-bunny-video) — виконано
- `T-033` (001-bunny-video) — виконано
- `T-035` (001-bunny-video) — виконано
- `T-010` (002-ai-tutor) — виконано
- `T-012` (002-ai-tutor) — виконано
- `T-020` (002-ai-tutor) — виконано
- `T-022` (002-ai-tutor) — виконано
- `T-024` (002-ai-tutor) — виконано
- `T-026` (002-ai-tutor) — у роботі
- `T-032` (002-ai-tutor) — у роботі
- `T-034` (002-ai-tutor) — у роботі
- `T-038` (002-ai-tutor) — у роботі
- `T-040` (002-ai-tutor) — у роботі
- `T-044` (002-ai-tutor) — у роботі
- `T-046` (002-ai-tutor) — у роботі
- `T-048` (002-ai-tutor) — у роботі
- `T-050` (002-ai-tutor) — у роботі
