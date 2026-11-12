"""Acceso a datos del microservicio.

Cada microservicio tiene su propia base de datos (patron database-per-service):
- En local, si no hay DATABASE_URL, se usa un archivo SQLite propio del servicio.
- En Render, DATABASE_URL apunta a PostgreSQL y cada servicio trabaja en su
  propio esquema (DB_SCHEMA), de modo que ninguno ve las tablas de otro.
"""
import os

from sqlalchemy import create_engine, text


def make_engine(service_name):
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        # Render entrega la URL como postgres:// o postgresql://; SQLAlchemy
        # necesita saber que driver usar (psycopg 3).
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                url = "postgresql+psycopg://" + url[len(prefix):]
                break
        schema = os.getenv("DB_SCHEMA", service_name)
        engine = create_engine(
            url,
            pool_pre_ping=True,
            pool_size=3,
            max_overflow=2,
            connect_args={"options": f"-csearch_path={schema}"},
        )
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        return engine

    data_dir = os.getenv("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
    os.makedirs(data_dir, exist_ok=True)
    return create_engine(f"sqlite:///{os.path.join(data_dir, service_name + '.db')}")


def rows(result):
    return [dict(r._mapping) for r in result]


def one(result):
    r = result.first()
    return dict(r._mapping) if r else None
