# docker/edx-platform/Dockerfile — образ LMS/CMS з нашими XBlock-плагінами.
#
# Базується на офіційному openedx-образі Tutor 18 (Redwood) і повторює наш
# Tutor-патч `openedx-dockerfile-post-python-requirements`:
#   video_xblock  (фіча 001, FR-001-07)
#   ai_tutor_xblock (фіча 002, FR-002-10)
# Потім виконується collectstatic, щоб статичні файли XBlock потрапили
# в /openedx/staticfiles (їх віддає uwsgi через static-map).
#
# Збірка (один раз, ~30-60 хв через collectstatic):
#   docker compose build lms
#
# Продакшн-настройки монтуються в рантаймі з ./docker/openedx-settings
# і ./docker/openedx-config (див. docker-compose.yml), тож секрети
# в образ НЕ запікаються (для collectstatic використовуються dummy-значення).

ARG OPENEDX_VERSION=18.1.3
FROM docker.io/overhangio/openedx:${OPENEDX_VERSION}

# Базовий образ працює від користувача app з venv /openedx/venv (записуваний).
COPY --chown=app:app ./video_xblock /opt/xblocks/video_xblock
COPY --chown=app:app ./ai_tutor_xblock /opt/xblocks/ai_tutor_xblock
RUN pip install --no-cache-dir /opt/xblocks/video_xblock /opt/xblocks/ai_tutor_xblock

# Build-time копія налаштувань і конфігу суто для collectstatic.
# Рантайм монтує справжні файли поверх (див. volumes у docker-compose.yml).
COPY ./docker/openedx-settings/lms /openedx/edx-platform/lms/envs/tutor
COPY ./docker/openedx-settings/cms /openedx/edx-platform/cms/envs/tutor
COPY ./docker/openedx-config /openedx/config-build
ENV LMS_CFG=/openedx/config-build/lms.env.yml \
    CMS_CFG=/openedx/config-build/cms.env.yml \
    REVISION_CFG=/openedx/config-build/revisions.yml \
    DJANGO_SETTINGS_MODULE=lms.envs.tutor.production \
    OPENEDX_SECRET_KEY=buildtime-dummy-secret-key-not-for-production \
    JWT_PRIVATE_SIGNING_JWK_JSON={} \
    JWT_PUBLIC_SIGNING_JWK_SET_JSON='{"keys":[]}' \
    JWT_COMMON_SECRET_KEY=buildtime-dummy \
    CMS_OAUTH2_SECRET=buildtime-dummy \
    MYSQL_PASSWORD=buildtime-dummy \
    AI_TUTOR_SHARED_SECRET=buildtime-dummy

RUN ./manage.py lms collectstatic --noinput --settings=lms.envs.tutor.production \
    && ./manage.py cms collectstatic --noinput --settings=cms.envs.tutor.production

# Default CMD береться з базового образу: ["uwsgi", "/openedx/uwsgi.ini"]
