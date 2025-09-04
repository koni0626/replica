# syntax=docker/dockerfile:1

# Base image
FROM python:3.11-slim AS base

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install system deps (build tools are useful for some Python wheels)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential \
       curl \
    && rm -rf /var/lib/apt/lists/*

# Workdir
WORKDIR /app

# Install Python deps first (leverage Docker layer caching)
COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . /app

# Create a non-root user (optional but recommended)
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# App port
ENV PORT=8000
EXPOSE 8000

# Default DB (override with -e DATABASE_URL)
ENV DATABASE_URL=sqlite:///sample.db \
    SECRET_KEY=change_me

# Run with Gunicorn using the Flask application factory
# app.py exposes create_app() so we can pass it directly to gunicorn
CMD ["gunicorn", "-w", "4", "-k", "gthread", "--threads", "8", "-b", "0.0.0.0:8000", "app:create_app()"]
