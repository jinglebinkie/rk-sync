# --- BUILD STAGE ---
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build tools including Rust for pydantic-core
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

# Install Python dependencies into a temporary folder
RUN pip install --no-cache-dir --prefix=/install \
    google-api-python-client \
    google-auth-httplib2 \
    google-auth-oauthlib \
    surrealdb \
    playwright

# --- FINAL STAGE ---
FROM python:3.11-slim

WORKDIR /app

# Copy compiled dependencies from builder
COPY --from=builder /install /usr/local

# Install ONLY the system dependencies for Firefox and the Firefox binary
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgtk-3-0 \
    libasound2 \
    libdbus-glib-1-2 \
    libxt6 \
    && playwright install firefox \
    && playwright install-deps firefox \
    && rm -rf /var/lib/apt/lists/*

COPY sync_worker.py .
RUN mkdir /data && chmod 777 /data

CMD ["python", "-u", "sync_worker.py"]