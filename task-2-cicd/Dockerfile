# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.12

# ---------- Stage 1: builder - resolve & install dependencies into a venv -----
FROM python:${PYTHON_VERSION}-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
RUN python -m venv /opt/venv
COPY requirements.txt .
RUN /opt/venv/bin/pip install -r requirements.txt

# ---------- Stage 2: test - the build FAILS if unit tests fail ----------------
# Not part of the final image; built explicitly in CI with --target test.
FROM python:${PYTHON_VERSION}-slim AS test
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY service ./service
COPY tests ./tests
RUN python -m unittest discover -s tests -t . -v

# ---------- Stage 3: runtime - minimal, non-root ------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime
ARG GIT_SHA=dev
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_VERSION=${GIT_SHA}

# Fixed numeric UID/GID so Kubernetes `runAsNonRoot` can verify it.
RUN groupadd --system --gid 10001 app \
 && useradd  --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY --chown=10001:10001 service ./service

USER 10001:10001
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2)"]

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--threads", "4", \
     "--access-logfile", "-", "service.main:application"]
