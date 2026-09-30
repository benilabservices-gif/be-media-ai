# syntax=docker/dockerfile:1

# ── Étape 1 : construction de l'environnement virtuel ──
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

# ── Étape 2 : image d'exécution, sans outils de build ──
FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system app && useradd --system --gid app --no-create-home app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY alembic.ini ./
COPY alembic ./alembic

USER app
EXPOSE 8000

# Même image pour l'API, les migrations (`alembic upgrade head`) et, plus tard, le worker
CMD ["uvicorn", "digital360.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
