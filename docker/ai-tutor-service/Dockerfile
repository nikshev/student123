# docker/ai-tutor-service/Dockerfile — внутрішній AI Tutor Service (FR-002-10).
FROM docker.io/python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=settings_production

WORKDIR /app

# Залежності сервісу: Django (як у проєктному venv — 4.2.x) + PyYAML.
RUN pip install --no-cache-dir "Django==4.2.30" "PyYAML==6.0.3"

# Код сервісу + production-налаштування + manage.py.
COPY ./ai_tutor_service /app/ai_tutor_service
COPY ./docker/ai-tutor-service/settings_production.py /app/settings_production.py
COPY ./docker/ai-tutor-service/manage.py /app/manage.py
COPY ./docker/ai-tutor-service/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh && mkdir -p /data

EXPOSE 8001
ENTRYPOINT ["/app/entrypoint.sh"]
