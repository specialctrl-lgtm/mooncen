import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import URL, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from DB.connection_settings import bounded_env_int, database_connect_options


load_dotenv(Path(__file__).resolve().parents[1] / ".env")

ENVIRONMENT = os.getenv("ENVIRONMENT", "development").strip().lower()
if ENVIRONMENT in {"prod", "production"}:
    api_user = os.getenv("DB_API_USER", "").strip()
    api_password = os.getenv("DB_API_PASSWORD", "")
    owner_user = (
        os.getenv("DB_OWNER_USER", "").strip()
        or os.getenv("DB_MIGRATOR_USER", "").strip()
        or os.getenv("DB_USER", "").strip()
    )
    if not api_user or not api_password:
        raise RuntimeError(
            "Production requires explicit DB_API_USER and DB_API_PASSWORD; "
            "the migration-owner DB_USER fallback is not allowed."
        )
    if owner_user and api_user == owner_user:
        raise RuntimeError("Production DB_API_USER must differ from the database owner/migration role.")


DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_API_USER", os.getenv("DB_USER", "mooncen_api"))
DB_PASS = os.getenv("DB_API_PASSWORD", os.getenv("DB_PASSWORD", ""))
DB_NAME = os.getenv("DB_NAME", "mooncen")
DB_PORT = bounded_env_int("DB_PORT", 5432, 1, 65535)

POOL_SIZE = bounded_env_int("DB_POOL_MIN", 2, 1, 100)
POOL_MAX = bounded_env_int("DB_POOL_MAX", 8, POOL_SIZE, 200)

SQLALCHEMY_DATABASE_URL = URL.create(
    "postgresql+psycopg2",
    username=DB_USER,
    password=DB_PASS,
    host=DB_HOST,
    port=DB_PORT,
    database=DB_NAME,
)

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_pre_ping=True,
    pool_size=POOL_SIZE,
    max_overflow=POOL_MAX - POOL_SIZE,
    pool_timeout=bounded_env_int("DB_POOL_TIMEOUT", 10, 1, 120),
    pool_recycle=bounded_env_int("DB_POOL_RECYCLE", 1_800, 60, 86_400),
    connect_args=database_connect_options(DB_HOST, "mooncen-api"),
)
SessionLocal = sessionmaker(autoflush=False, expire_on_commit=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
