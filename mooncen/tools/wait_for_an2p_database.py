#!/usr/bin/env python3
"""Wait for an an2p API or deployment-queue database to become ready."""

from __future__ import annotations

import argparse
import os
import sys
import time

import psycopg2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument(
        "--mode",
        choices=("api", "deployment-queue"),
        default="api",
    )
    args = parser.parse_args()
    deadline = time.monotonic() + max(1, args.timeout)
    last_error = "unavailable"
    while time.monotonic() < deadline:
        try:
            if args.mode == "deployment-queue":
                connection_settings = {
                    "host": os.environ["OPS_DEPLOY_QUEUE_DB_HOST"],
                    "port": int(os.environ["OPS_DEPLOY_QUEUE_DB_PORT"]),
                    "dbname": os.environ["OPS_DEPLOY_QUEUE_DB_NAME"],
                    "user": os.environ["OPS_DEPLOY_QUEUE_DB_USER"],
                    "password": os.environ["OPS_DEPLOY_QUEUE_DB_PASSWORD"],
                }
            else:
                connection_settings = {
                    "host": os.environ["DB_HOST"],
                    "port": int(os.environ["DB_PORT"]),
                    "dbname": os.environ["DB_NAME"],
                    "user": os.environ["DB_API_USER"],
                    "password": os.environ["DB_API_PASSWORD"],
                }
            with psycopg2.connect(
                **connection_settings,
                sslmode=os.getenv("DB_SSLMODE", "disable"),
                connect_timeout=3,
                application_name="mooncen-an2p-readiness",
            ) as connection:
                with connection.cursor() as cursor:
                    if args.mode == "deployment-queue":
                        cursor.execute(
                            """
                            SELECT to_regclass('public.ops_jobs') IS NOT NULL
                               AND to_regclass('public.ops_agents') IS NOT NULL
                               AND has_table_privilege(current_user, 'public.ops_jobs', 'SELECT')
                               AND has_table_privilege(current_user, 'public.ops_agents', 'SELECT')
                            """
                        )
                    else:
                        cursor.execute(
                            """
                            SELECT to_regclass('public.users') IS NOT NULL
                               AND to_regclass('public.courses') IS NOT NULL
                               AND has_table_privilege(current_user, 'public.users', 'SELECT')
                               AND has_table_privilege(current_user, 'public.courses', 'SELECT')
                            """
                        )
                    if cursor.fetchone() != (True,):
                        raise psycopg2.OperationalError("required schema or API grants are not ready")
            print(f"an2p {args.mode} database is ready")
            return 0
        except (KeyError, ValueError, psycopg2.Error) as exc:
            last_error = type(exc).__name__
            time.sleep(1)
    print(f"an2p development database readiness timed out ({last_error})", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
