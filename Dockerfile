# CodeLens CI: one image for the API (+ dashboard) and the Celery worker.

# --- Stage 1: build the React dashboard -------------------------------------
FROM node:22-slim AS dashboard
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- Stage 2: Python application ---------------------------------------------
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts
COPY benchmarks ./benchmarks
COPY --from=dashboard /frontend/dist ./frontend/dist

# Run as an unprivileged user: the worker processes untrusted repository archives.
RUN useradd --create-home --uid 10001 codelens && mkdir -p benchmarks/results && chown -R codelens benchmarks/results
USER codelens

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
