# syntax=docker/dockerfile:1.7
ARG PYTHON_VERSION=3.13.7

FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv

RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

COPY packages/python /build/common
COPY packages/science /build/science
COPY apps/worker /build/worker

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --upgrade pip \
    && python -m pip install /build/common /build/science /build/worker

FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    GDAL_CACHEMAX=256 \
    CPL_TMPDIR=/tmp \
    PROJ_NETWORK=OFF

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        libexpat1 \
        libgdal32 \
        libgeos-c1v5 \
        libproj25 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 spectral \
    && useradd --system --uid 10001 --gid spectral --home-dir /nonexistent --shell /usr/sbin/nologin spectral

COPY --from=builder /opt/venv /opt/venv
COPY --chmod=755 infrastructure/docker/worker-entrypoint.sh /usr/local/bin/spectral-worker-entrypoint

RUN python -c "import rasterio; from rasterio.env import Env; env = Env(); env.__enter__(); assert 'GTiff' in env.drivers(); print('Rasterio', rasterio.__version__, 'GDAL', rasterio.__gdal_version__); env.__exit__(None, None, None)"

WORKDIR /opt/spectral/worker
USER 10001:10001
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -m spectral_worker.healthcheck || exit 1

ENTRYPOINT ["spectral-worker-entrypoint"]
CMD ["ingestion"]
