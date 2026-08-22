FROM postgres:16.14-bookworm@sha256:64154d0babcb1741988719e703419af0382b19953706149f9872fbd0f438efa8

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        postgresql-16-postgis-3=3.6.4+dfsg-2.pgdg12+1 \
        postgresql-16-postgis-3-scripts=3.6.4+dfsg-2.pgdg12+1; \
    test -f /usr/share/postgresql/16/extension/postgis.control; \
    test -f /usr/share/postgresql/16/extension/uuid-ossp.control; \
    test -f /usr/share/postgresql/16/extension/pg_trgm.control; \
    test -f /usr/share/postgresql/16/extension/pgcrypto.control; \
    rm -rf /var/lib/apt/lists/*
