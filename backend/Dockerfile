# syntax=docker/dockerfile:1
# api image: FastAPI + compiled frontend. Build context is the repository root.
# Base images are pinned by digest; update them with the procedure in docs/OPERATIONS.md.

ARG NODE_IMAGE=node:24-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20
ARG PYTHON_IMAGE=python:3.13-slim-trixie@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f

# ---------------------------------------------------------------- frontend build
FROM ${NODE_IMAGE} AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund --ignore-scripts
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------- api runtime
FROM ${PYTHON_IMAGE} AS api
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MC_DATA_DIR=/data \
    MC_WORK_DIR=/work \
    MC_STATIC_DIR=/app/static
RUN groupadd -g 10001 app && useradd -u 10001 -g app -M -d /nonexistent -s /usr/sbin/nologin app
COPY backend/requirements.lock /tmp/requirements.lock
RUN pip install --no-deps --require-hashes -r /tmp/requirements.lock && rm /tmp/requirements.lock
RUN mkdir -p /data /work /app && chown app:app /data /work && chmod 0700 /data /work
WORKDIR /app
COPY backend/app /app/app
COPY --from=frontend /src/dist /app/static
RUN python -m compileall -q /app/app
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000", \
     "--no-server-header", "--timeout-keep-alive", "5", "--limit-concurrency", "64"]

# ---------------------------------------------------------------- api tests
FROM api AS api-test
USER root
COPY backend/requirements-test.lock /tmp/requirements-test.lock
RUN pip install --no-deps --require-hashes -r /tmp/requirements-test.lock && rm /tmp/requirements-test.lock
COPY backend/tests /app/tests
COPY backend/pytest.ini /app/pytest.ini
USER 10001:10001
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/api", "tests/common"]
