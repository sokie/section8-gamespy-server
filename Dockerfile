# Section 8 GameSpy backend. Standard library only, so the image is the slim Python base and the code.
# docs/hosting.md covers the ports, the volume, the compose file and the automatic updates.
FROM python:3.12-slim

ARG VERSION=dev
ARG VCS_REF=unknown
ARG BUILD_ID=local

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    SECTION8_IMAGE_VERSION=$VERSION \
    SECTION8_IMAGE_REVISION=$VCS_REF \
    SECTION8_IMAGE_BUILD=$BUILD_ID

COPY . /app
# A new named volume copies the ownership of /data from the image, so the server can write to it.
RUN python -m compileall -q /app/server /app/scripts \
    && useradd --system --uid 10001 --no-create-home --home-dir /data section8 \
    && mkdir /data \
    && chown section8:section8 /data
# The code is read-only in /app. Everything runs from the volume, `docker exec` tools included, so a
# relative path (reports/, config.json, section8.db, a tool's log) lands on /data.
WORKDIR /data
USER section8

# 8901-8906: the XLSP services (GPSP, GPCM, Auth, Competition, Sake, MOTD). 27900/udp: availability check.
# 8080: the stats portal, best served through a reverse proxy.
EXPOSE 8901-8906/tcp 27900/udp 8080/tcp

# Python turns SIGINT into KeyboardInterrupt, so `docker stop` ends the server at once. SIGTERM would wait
# for the stop timeout, then SIGKILL.
STOPSIGNAL SIGINT
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-m", "server.container", "--health"]
ENTRYPOINT ["python", "-m", "server.container"]
