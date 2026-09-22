# Open edX + наші плагіни в Docker Compose (без Tutor CLI)
#
# Що піднімається (`docker compose up -d`):
#   LMS   http://local.openedx.io:8010   (Open edX Redwood, образ 18.1.3)
#   CMS   http://studio.local.openedx.io:8011 (Studio)
#   AI Tutor Service (внутрішній): http://ai-tutor-service:8001 (хост: :8002)
# Хостові порти LMS/CMS задаються через LMS_PORT/CMS_PORT у .env (дефолт 8010/8011).
# Плагіни: video_xblock (фіча 001), ai_tutor_xblock + ai_tutor_service (фіча 002)
# запечені в образ + налаштовані через docker/openedx-settings.
#
# Архітектура файлів:
#   docker-compose.yml              — усі сервіси
#   .env / .env.example             — секрети (тільки .env.example у git)
#   docker/edx-platform/Dockerfile  — openedx-образ + pip install наших XBlock + collectstatic
#   docker/openedx-config/          — lms.env.yml / cms.env.yml / revisions.yml (без секретів)
#   docker/openedx-settings/        — lms|/cms production.py (секрети з env) + uwsgi.ini
#   docker/ai-tutor-service/        — Dockerfile + manage.py + settings + entrypoint
#   docker/init/                    — init-secrets.sh, pem_to_jwk.py, edx-init.sh
#   docker/redis/redis.conf         — конфіг Redis (копія Tutor 18)
#
# Джерела істини (версії зафіксовано за Tutor 18.1.3 / Redwood):
#   openedx 18.1.3, mysql 8.4.0, mongo 7.0.7, redis 7.2.4,
#   elasticsearch 7.17.13, exim-relay 4.96-r1-0.

## Швидкий старт (перший запуск, ~40-70 хв: збірка образу + collectstatic + міграції)

```bash
cp .env.example .env
./docker/init/init-secrets.sh        # генерує секрети у .env (openssl/ssh-keygen)
# вписати AI_TUTOR_LLM_API_KEY у .env (ключ Anthropic, інакше /ask → 502)
docker compose build                 # збірка edxapp-образа з XBlock (~30-60 хв)
docker compose up -d                 # БД → edx-init (міграції, OAuth, admin) → lms/cms/workers + ai-tutor-service
docker compose logs -f edx-init      # дочекатися "Ініціалізація завершена"
```

Відкрити: LMS http://local.openedx.io:8010, Studio http://studio.local.openedx.io:8011
(`local.openedx.io` резолвиться в 127.0.0.1 без записів у /etc/hosts).
Логін: ADMIN_USERNAME/ADMIN_PASSWORD з .env.

## Що далі (вручну, як у quickstart фіч)

- 001 (Bunny video): у Studio додати Video XBlock, вписати Bunny Stream ключі
  (BUNNY_STREAM_* — див. specs/001-bunny-video/quickstart.md). Самі ключі Bunny
  прокидаються через Tutor-плагін; для compose-версії додай їх у .env і
  пробрось у lms/cms за потреби.
- 002 (AI Tutor): у Studio додати AI Tutor XBlock до юніту; матеріали
  інгestяться staff-користувачем за specs/002-ai-tutor/quickstart.md
  (через внутрішній http://ai-tutor-service:8001, секрет AI_TUTOR_SHARED_SECRET).
- AI_TUTOR_LLM_API_KEY порожній → /ask повертає 502 (контрольовано, за контрактом).

## Корисні команди

```bash
docker compose ps
docker compose logs -f lms cms ai-tutor-service
docker compose run --rm edx-init          # повторити ініціалізацію (ідемпотентно)
docker compose exec lms ./manage.py lms createsuperuser --settings=lms.envs.tutor.production
docker compose down            # зупинити (дані у volumes)
docker compose down -v          # зупинити І видалити всі дані
```

## Нотатки

- Elasticsearch heap 1g (як у Tutor 18). Якщо мало RAM — вимкни сервіс
  elasticsearch з compose і встанови FEATURES ENABLE_COURSE_DISCOVERY=false
  (потребує правки lms.env.yml).
- Це demo-розгортання (runserver для ai-tutor-service, DEBUG=false для LMS/CMS,
  секрети у .env). Для продакшену — Tutor flow з tutor-plugin/ цього репо.
- Зміна коду XBlock/сервісу вимагає перезбірки образу (`docker compose build`)
  або `docker compose up -d --build`.
