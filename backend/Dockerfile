# syntax=docker/dockerfile:1.6
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

ENV TESSERACT_CMD=/usr/bin/tesseract

COPY pyproject.toml requirements.txt ./
COPY backend/requirements.txt backend/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt -r backend/requirements.txt

COPY flow_solver ./flow_solver
COPY backend ./backend
COPY docs ./docs
COPY app.py README.md ./

EXPOSE 8000

CMD ["python", "app.py"]
