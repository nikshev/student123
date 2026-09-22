#!/bin/bash
# docker/init/edx-init.sh — одноразова ініціалізація Open edX.
# Запуск: docker compose run --rm edx-init  (після підняття mysql/mongodb/redis)
# Або автоматично: сервіси lms/cms чекають service_completed_successfully.
# Ідемпотентний: повторний запуск безпечний.
# Дзеркалить Tutor jobs tutor/templates/jobs/init/{mysql,lms,cms}.sh (Tutor 18).
set -euo pipefail

wait_tcp() { # host port [tries]
  local host="$1" port="$2" tries="${3:-30}" i=0
  until (echo > "/dev/tcp/${host}/${port}") >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -ge "$tries" ]; then echo "Час вийшов: ${host}:${port}" >&2; exit 1; fi
    echo "Чекаю ${host}:${port} (${i}/${tries})..."
    sleep 5
  done
}

echo "== MySQL =="
wait_tcp mysql 3306
mysql -u root --password="${MYSQL_ROOT_PASSWORD}" --host mysql --port 3306 \
  -e "CREATE DATABASE IF NOT EXISTS \`${MYSQL_DATABASE}\`;"
mysql -u root --password="${MYSQL_ROOT_PASSWORD}" --host mysql --port 3306 \
  -e "CREATE USER IF NOT EXISTS '${MYSQL_USER}';"
mysql -u root --password="${MYSQL_ROOT_PASSWORD}" --host mysql --port 3306 \
  -e "ALTER USER '${MYSQL_USER}'@'%' IDENTIFIED BY '${MYSQL_PASSWORD}';"
mysql -u root --password="${MYSQL_ROOT_PASSWORD}" --host mysql --port 3306 \
  -e "GRANT ALL ON \`${MYSQL_DATABASE}\`.* TO '${MYSQL_USER}'@'%';"

echo "== MongoDB / Redis =="
wait_tcp mongodb 27017
wait_tcp redis 6379

echo "== Міграції LMS =="
./manage.py lms migrate --noinput

echo "== Міграції CMS =="
# УВАГА: DJANGO_SETTINGS_MODULE сервісу — lms.envs.tutor.production,
# тому для CMS-команд модуль підміняємо явно (інакше мігрує LMS-схема).
DJANGO_SETTINGS_MODULE=cms.envs.tutor.production ./manage.py cms migrate --noinput

CMS_URL="http://${CMS_HOST:-studio.local.openedx.io}:${CMS_PORT:-8011}"
echo "== OAuth-клієнт CMS SSO (cms-sso) =="
./manage.py lms manage_user cms cms@openedx --unusable-password \
  || true
./manage.py lms create_dot_application \
  --grant-type authorization-code \
  --redirect-uris "${CMS_URL}/complete/edx-oauth2/" \
  --client-id "${CMS_OAUTH2_KEY_SSO:-cms-sso}" \
  --client-secret "${CMS_OAUTH2_SECRET}" \
  --scopes user_id \
  --skip-authorization \
  --update cms-sso cms \
 

echo "== Суперкористувач ${ADMIN_USERNAME} =="
./manage.py lms manage_user "${ADMIN_USERNAME}" "${ADMIN_EMAIL}" \
  --staff --superuser --unusable-password \
  2>/dev/null || true
./manage.py lms shell <<PYEOF
from django.contrib.auth import get_user_model
User = get_user_model()
u = User.objects.get(username="${ADMIN_USERNAME}")
u.set_password("${ADMIN_PASSWORD}")
u.save(update_fields=["password"])
print("admin password set")
PYEOF

echo "== Waffle: completion tracking =="
(./manage.py lms waffle_switch --list \
  | grep completion.enable_completion_tracking) \
  || ./manage.py lms waffle_switch --create completion.enable_completion_tracking on \
 

echo "Ініціалізація завершена."
