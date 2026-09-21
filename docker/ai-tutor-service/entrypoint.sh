#!/bin/bash
# docker/ai-tutor-service/entrypoint.sh — міграції SQLite і запуск API (порт 8001).
# Дзеркалить tutor-plugin патч openedx-ai-tutor-service-compose (runserver).
set -euo pipefail

# Явні аргументи (docker run ... <cmd>) виконуються як є — для manage.py/diagnостики.
if [ "$#" -gt 0 ]; then
  exec "$@"
fi

python /app/manage.py migrate --noinput
exec python /app/manage.py runserver 0.0.0.0:8001
