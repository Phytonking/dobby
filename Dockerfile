# syntax=docker/dockerfile:1
FROM python:3.14-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home bot
COPY bot ./bot
COPY scripts/link_google.py ./scripts/link_google.py

# Test image: adds the suite and its config. Never the deployed stage.
FROM base AS test
COPY pyproject.toml ./
COPY scripts ./scripts
COPY tests ./tests
ENV PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp
USER 10001
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--basetemp=/tmp/pytest"]

# Deployed stage stays last so a plain `docker build .` selects it.
FROM base AS runtime
USER 10001
CMD ["python", "-m", "bot.main"]
