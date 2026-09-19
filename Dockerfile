# ==========================================
# Multi-stage Dockerfile for AxonBot
# ==========================================

# Stage 1: Build & Dependencies
FROM python:3.11-slim AS builder

WORKDIR /build
ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends     gcc     libpq-dev     && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Stage 2: Minimal Runtime
FROM python:3.11-slim AS runner

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1     PATH=/root/.local/bin:$PATH

COPY --from=builder /root/.local /root/.local

# Копируем только необходимые файлы сервиса
COPY Scripts/bot /app/Scripts/bot
COPY Tests /app/Tests
COPY requirements.txt /app/

# Создаем папки данных и кэша
RUN mkdir -p /app/Scripts/bot/data/logs /app/Scripts/bot/data/media_cache

# Проверка работоспособности
HEALTHCHECK --interval=60s --timeout=10s --retries=3   CMD python -c "import aiosqlite, aiogram; print('Healthy')" || exit 1

ENTRYPOINT ["python", "-m", "Scripts.bot.main"]
