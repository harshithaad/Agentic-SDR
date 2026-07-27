# Backend image — one artifact, many roles (api / worker <stage> / scheduler).
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY migrations/ migrations/

RUN useradd --uid 10001 --no-create-home sdr && chown -R sdr /srv
USER sdr

EXPOSE 8000 9100

# default role: API. Workers/scheduler override the command.
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
