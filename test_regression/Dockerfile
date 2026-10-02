FROM dobby-regression-bot:local
USER root
COPY dashboard/requirements.txt /tmp/dashboard-requirements.txt
RUN pip install --no-cache-dir -r /tmp/dashboard-requirements.txt playwright==1.55.0 pytest-asyncio==1.2.0 \
    && playwright install --with-deps chromium
COPY dashboard /app/dashboard
COPY migrations /app/migrations
COPY pyproject.toml /app/pyproject.toml
RUN pip install --no-cache-dir aiosqlite
COPY test_regression /app/test_regression
ENV PYTHONPATH=/app PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright
