from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

# pool_pre_ping=True: before handing a connection from the pool to your code,
# SQLAlchemy sends a cheap "SELECT 1" to verify the connection is still alive.
# Without this, a connection PostgreSQL silently dropped (e.g. after idle timeout)
# surfaces as a cryptic operational error inside your business logic instead of
# being transparently recycled here.
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """
    FastAPI dependency. Yields one DB session per request, guaranteed to
    close even if the request handler raises an exception.
    Usage:
        @router.get("/example")
        def example(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()