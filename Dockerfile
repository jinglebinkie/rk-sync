FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

WORKDIR /app

RUN pip install --no-cache-dir \
    google-api-python-client \
    google-auth-httplib2 \
    google-auth-oauthlib \
    playwright

COPY sync_worker.py .

# Playwright requires a one-time install of the browser binary
RUN playwright install chromium

# Create data dir for SQLite persistence
RUN mkdir /data && chmod 777 /data

CMD ["python", "sync_worker.py"]