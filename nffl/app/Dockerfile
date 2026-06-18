FROM python:3.12-slim

WORKDIR /app

# Keep image deterministic and small
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy project files last (better build caching)
COPY . /app

# Default command (can be overridden)
CMD ["python", "scripts/yahoo/auth.py"]
