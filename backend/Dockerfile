FROM python:3.12-alpine3.24
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN apk upgrade --no-cache && pip install --no-cache-dir -r requirements.txt
RUN addgroup -S app && adduser -S -G app -h /app app
COPY --chown=app:app backend backend
COPY --chown=app:app migrations migrations
COPY --chown=app:app alembic.ini .
USER app
CMD ["sh", "-c", "python -m alembic upgrade head && uvicorn backend.main:app --host 0.0.0.0 --port 8000"]

