FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install HandBrakeCLI, ffmpeg (ffprobe), and curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    handbrake-cli \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy worker application code
COPY worker_app/ /app/worker_app/

# Local work directory for downloading segments and transcoding
RUN mkdir -p /tmp/transcode_worker

CMD ["python", "-m", "worker_app.worker"]
