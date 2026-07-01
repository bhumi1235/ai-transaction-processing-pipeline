from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import settings

# Create engine
# If using Postgres, no special args needed, but for celery and multithreading pool pre-ping is good
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True
)

# Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Declarative base
Base = declarative_base()


def get_db():
    """Dependency injection helper to yield database session and ensure clean close."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
