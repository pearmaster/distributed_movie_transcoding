FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install ffmpeg, mkvtoolnix, and curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    mkvtoolnix \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy coordinator app code
COPY coordinator_app/ /app/coordinator_app/

# Create mount point directories
RUN mkdir -p /input /output /data

EXPOSE 8000

CMD ["uvicorn", "coordinator_app.main:app", "--host", "0.0.0.0", "--port", "8000"]
