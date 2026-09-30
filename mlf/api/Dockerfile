FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/api_src:/app/mlf_src

COPY api/requirements.txt /tmp/api-requirements.txt

RUN pip install --no-cache-dir -r /tmp/api-requirements.txt

COPY api/src /app/api_src
COPY api/tests /app/tests
COPY app/src /app/mlf_src
COPY scripts/yahoo /app/scripts/yahoo

CMD ["uvicorn", "mlf_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]