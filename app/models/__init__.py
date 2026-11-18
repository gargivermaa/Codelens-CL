# Importing every model here means a single `import app.models` in
# alembic/env.py registers all tables with SQLAlchemy's metadata.
# Forgetting to import a new model here is the #1 cause of
# "alembic revision --autogenerate produces an empty migration."
from app.models.database import Base
from app.models.finding import Finding
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.retrieval_log import RetrievalLog
from app.models.review import Review
from app.models.review_file import ReviewFile

__all__ = ["Base", "Repository", "PullRequest", "Review", "Finding", "RetrievalLog", "ReviewFile"]
