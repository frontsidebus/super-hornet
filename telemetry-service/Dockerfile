FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY telemetry/ telemetry/

RUN pip install --no-cache-dir .

# Create data directory for state persistence
RUN mkdir -p /data

EXPOSE 3839

CMD ["uvicorn", "telemetry.service:app", "--host", "0.0.0.0", "--port", "3839"]
