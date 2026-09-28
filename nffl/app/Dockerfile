FROM python:3.12-slim

WORKDIR /app

# NFFL rollover safety backups require pg_dump / pg_restore.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Keep image deterministic and small
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy project files last (better build caching)
COPY . /app

# Default command (can be overridden)
CMD ["python", "scripts/yahoo/auth.py"]
