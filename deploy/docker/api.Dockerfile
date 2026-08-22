FROM python:3.13.14-slim-bookworm@sha256:67a1e1f215ccda113cfc024e8639049257e88f273898f595b61476d128d387e8

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --gid 10001 mooncen \
    && useradd --uid 10001 --gid mooncen --create-home --home-dir /home/mooncen \
        --shell /usr/sbin/nologin mooncen

COPY requirements.txt requirements.lock ./
RUN python -m pip install --require-hashes -r requirements.lock \
    && python -m pip check

COPY --chown=mooncen:mooncen backend ./backend
COPY --chown=mooncen:mooncen DB ./DB
COPY --chown=mooncen:mooncen Crawler ./Crawler
COPY --chown=mooncen:mooncen config ./config
COPY --chown=mooncen:mooncen deploy/__init__.py ./deploy/__init__.py
COPY --chown=mooncen:mooncen \
    deploy/docker/__init__.py \
    deploy/docker/provision_api_login.py \
    deploy/docker/release_manifest.py \
    deploy/docker/verify_release_bundle.py \
    ./deploy/docker/
COPY --chown=mooncen:mooncen ops_agent ./ops_agent
COPY --chown=mooncen:mooncen tools ./tools
COPY --chown=mooncen:mooncen utils ./utils
COPY --chown=mooncen:mooncen \
    ai_processor.py \
    data_parser.py \
    description_cleaner.py \
    run_ai_pipeline.py \
    run_crawlers.py \
    service_group.py \
    target_category_fallback.py \
    target_cleaner.py \
    title_cleaner.py \
    utils.py \
    ./

USER mooncen

EXPOSE 8001

CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8001"]
