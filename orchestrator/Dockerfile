# Super Hornet Orchestrator - Multi-stage Dockerfile
# Builds the Python orchestration layer that coordinates all Super Hornet services.

# =============================================================================
# Stage 1: Build - install Python dependencies
# =============================================================================
FROM python:3.12-slim AS build

WORKDIR /build

# Install build-time system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy project metadata and source (hatchling needs the package to build)
COPY pyproject.toml .
COPY orchestrator/ ./orchestrator/

# Install dependencies into a virtual environment so we can copy them cleanly
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

# =============================================================================
# Stage 2: Runtime - slim image with only what we need
# =============================================================================
FROM python:3.12-slim AS runtime

# System dependencies for audio processing and general operation
RUN apt-get update && apt-get install -y --no-install-recommends \
    libportaudio2 \
    libsndfile1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy the virtual environment from the build stage
COPY --from=build /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Create non-root user for security
RUN groupadd --gid 1000 hornet \
    && useradd --uid 1000 --gid hornet --create-home hornet

WORKDIR /app

# Copy application source
COPY orchestrator/ ./orchestrator/
COPY pyproject.toml .

# Ensure the app directory is owned by our non-root user
RUN chown -R hornet:hornet /app

USER hornet

EXPOSE 3838

ENTRYPOINT ["python", "-m", "orchestrator.main"]
