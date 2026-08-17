# syntax=docker/dockerfile:1

FROM node:22-bookworm-slim AS node-runtime

FROM python:3.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node

RUN ln -s /usr/local/bin/node /usr/local/bin/nodejs \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        libatomic1 \
    && rm -rf /var/lib/apt/lists/* \
    && ffmpeg -version \
    && ffprobe -version \
    && node --version

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

RUN groupadd --gid 10001 bot \
    && useradd --uid 10001 --gid bot --create-home bot \
    && mkdir -p /data \
    && chown bot:bot /data

COPY --chown=bot:bot . .

USER bot

CMD ["python", "main.py"]
