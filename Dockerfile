# Build the React interface, then package it with the Django application.
FROM node:22-alpine AS frontend
WORKDIR /src/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --upgrade pip && pip install -r requirements.txt

COPY . ./
COPY --from=frontend /src/fakeaccountapp/static/react/ ./fakeaccountapp/static/react/
RUN DJANGO_DEBUG=True \
    DJANGO_SECRET_KEY=docker-build-only-secret-key-value-with-more-than-fifty-characters \
    DJANGO_ALLOWED_HOSTS=localhost \
    BACKUP_ACTIVE_KEY_ID=build \
    python manage.py collectstatic --noinput \
    && addgroup --system app \
    && adduser --system --ingroup app app \
    && mkdir -p /app/secure_backups \
    && chown -R app:app /app

USER app
EXPOSE 8000
CMD ["gunicorn", "fakeaccount.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--access-logfile", "-", "--error-logfile", "-"]
