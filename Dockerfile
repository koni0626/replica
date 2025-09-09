# SystemGen Dockerfile (named volumes version)
# - Python 3.10.11
# - Expose port 5000
# - Use named volumes for /docs and /app/instance

FROM python:3.10.11-slim

# Basic envs
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHON_PATH=/app \
    TZ=Asia/Tokyo \
    OPENAI_API_KEY=""

# Workdir (app install path)
WORKDIR ${PYTHON_PATH}

# System deps (git is required for git diff in DiffService)
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install deps (requirements.txt must be UTF-8)
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY . .

# Ensure instance dir exists (SQLite replica.db will reside here)
RUN mkdir -p ${PYTHON_PATH}/instance

# Named volume mount points (actual volume is defined by docker-compose)
VOLUME ["/docs"]
VOLUME ["${PYTHON_PATH}/instance"]

# Expose Flask port
EXPOSE 5000

# Normalize line endings to LF and make entrypoint executable
RUN sed -i 's/\r$//' ${PYTHON_PATH}/docker-entrypoint.sh && chmod +x ${PYTHON_PATH}/docker-entrypoint.sh

# Use entrypoint to run DB migrations before starting the app
ENTRYPOINT ["/app/docker-entrypoint.sh"]
# Start without Gunicorn (per requirement)
CMD ["python", "app.py"]
# Start without Gunicorn (per requirement)
CMD ["python", "app.py"]
