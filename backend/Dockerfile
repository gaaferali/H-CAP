FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --retries 5 --timeout 300 -r requirements.txt

COPY . .

RUN addgroup --system hcap && adduser --system --ingroup hcap hcap \
    && mkdir -p /app/media /app/staticfiles \
    && chown -R hcap:hcap /app

USER hcap

EXPOSE 8000

CMD ["sh", "-c", "uvicorn config.asgi:application --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY:-1} --timeout-graceful-shutdown 30"]
