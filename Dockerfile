# Dockerfile — Turnkey Appliance for rest_proxy GraphRAG Platform
FROM python:3.14-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CARGO_PROFILE_RELEASE_STRIP=false \
    TSLP_OFFLINE=1 \
    LM_PROXY_TOOL_PROFILE=primary

WORKDIR /app

# Install system build dependencies and runtime tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    libpq-dev \
    netcat-traditional \
    ripgrep \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency specifications first for layer caching
COPY requirements.txt requirements-ci.txt ./

# Install Python dependencies
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt

# Copy repository source code
COPY . .

# Ensure entrypoint script is executable
RUN chmod +x scripts/docker_entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

ENTRYPOINT ["scripts/docker_entrypoint.sh"]
