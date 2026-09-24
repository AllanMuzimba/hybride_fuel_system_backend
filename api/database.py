"""
Database connection setup.

Reads the connection string from the DATABASE_URL environment variable, e.g.:
    postgresql+psycopg2://fuel_user:fuel_pass@localhost:5432/fuel_system
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://fuel_user:fuel_pass@localhost:5432/fuel_system",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency: yields a DB session per-request and closes it after."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
