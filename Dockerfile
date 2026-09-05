# NGTeco Clock Manager — headless Linux service (PHASE 16).
#
# Build:   docker build -t ngteco-clock-manager .
# Run:     docker run -d --name clockmanager -p 8080:8080 -v ./data:/data ngteco-clock-manager
# Compose: docker compose up -d   (see docker-compose.yml; Synology Container
#          Manager imports the same file via "Create Project").
#
# The image ships the GUI-free core only: MB1 protocol, 120-byte parser,
# attendance engine, live capture, reconciliation and persistence. PySide6 is
# never installed here. Device profiles are created by the Windows GUI (or by
# seeding config); the container only reconciles them.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CLOCKMANAGER_DATA_DIR=/data \
    CLOCKMANAGER_LOG_TO_CONSOLE=1 \
    CLOCKMANAGER_SERVICE_HEALTH_BIND=0.0.0.0:8080

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

# Runtime dependencies only: no gui extra (PySide6/Qt never enters the image),
# no dev extra. pyzk==0.9 is the proven protocol transport (PROTOCOL.md).
RUN pip install --no-cache-dir . \
    && rm -rf /root/.cache

RUN useradd --system --create-home --home-dir /home/clockmanager clockmanager \
    && mkdir -p /data \
    && chown -R clockmanager:clockmanager /data /home/clockmanager

VOLUME /data
EXPOSE 8080

# Container health: liveness probe against the service's own /health endpoint.
# start-period allows the first sync pass to complete before marking unready.
HEALTHCHECK --interval=60s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4)" || exit 1

USER clockmanager
ENTRYPOINT ["clockmanager", "--serve"]
