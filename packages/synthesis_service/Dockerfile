FROM python:3.12-slim-bookworm

WORKDIR /app

# Install Node.js 22 LTS (required for OpenCode CLI)
# Use the official NodeSource setup for Debian bookworm (stable, no segfaults)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates gnupg && \
    mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" > /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Install OpenCode CLI and the OpenAI-compatible provider adapter
RUN npm install -g opencode-ai @ai-sdk/openai-compatible

# Install Python service dependencies
COPY packages/synthesis_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install the shared package with its own runtime dependencies.
COPY packages/shared/pyproject.toml /tmp/sprout-shared/pyproject.toml
COPY packages/shared/sprout_shared/ /tmp/sprout-shared/sprout_shared/
RUN pip install --no-cache-dir /tmp/sprout-shared

# Configure OpenCode
RUN mkdir -p /root/.config/opencode
COPY packages/synthesis_service/opencode.json /root/.config/opencode/opencode.json

# Create runtime directories
RUN mkdir -p /app/logs /tmp/opencode_workspace

# Copy service code and shared package
COPY packages/synthesis_service/sprout_synthesis/ /app/sprout_synthesis/
COPY packages/shared/sprout_shared/ /app/sprout_shared/

# SPROUT_SYNTHESIS_CALLBACK_URL must be injected by the deployer (k8s Deployment env,
# docker-compose, etc). Settings.callback_url enforces this when SPROUT_ENV!=dev.
ENV SPROUT_SYNTHESIS_LOG_DIR=/app/logs \
    SPROUT_SYNTHESIS_WORKSPACE_DIR=/tmp/opencode_workspace \
    PYTHONPATH=/app

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8002/health')" || exit 1

CMD ["uvicorn", "sprout_synthesis.main:app", "--host", "0.0.0.0", "--port", "8002"]
