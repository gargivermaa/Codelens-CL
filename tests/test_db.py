"""
Day 3-4 test suite — verifies the persistence layer against a real PostgreSQL
instance. No mocking. If these pass, the schema, FK constraints, and nullable
fields are correct.
"""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.models.database import Base
from app.models.repository import Repository
from app.models.pull_request import PullRequest
from app.models.review import Review
from app.models.retrieval_log import RetrievalLog
TEST_DATABASE_URL = (
    settings.DATABASE_URL.rsplit("/", 1)[0] + "/codelens_test"
)


@pytest.fixture(scope="module")
def db():
    """
    Uses tables created by Alembic.
    Cleans test records without deleting application tables.
    """
    engine = create_engine(TEST_DATABASE_URL)
    Session = sessionmaker(bind=engine)
    session = Session()

    session.query(RetrievalLog).delete(synchronize_session=False)
    session.query(Review).delete(synchronize_session=False)
    session.query(PullRequest).delete(synchronize_session=False)
    session.query(Repository).delete(synchronize_session=False)
    session.commit()

    yield session

    session.rollback()
    session.query(RetrievalLog).delete(synchronize_session=False)
    session.query(Review).delete(synchronize_session=False)
    session.query(PullRequest).delete(synchronize_session=False)
    session.query(Repository).delete(synchronize_session=False)
    session.commit()

    session.close()
    engine.dispose()


def test_insert_and_read_repository(db):
    """Basic write + read confirms SQLAlchemy mapping and DB connectivity."""
    repo = Repository(github_id=111, full_name="octocat/hello-world")
    db.add(repo)
    db.commit()
    db.refresh(repo)

    fetched = db.query(Repository).filter_by(github_id=111).first()
    assert fetched is not None
    assert fetched.full_name == "octocat/hello-world"
    assert fetched.id is not None        # PK was auto-assigned
    assert fetched.indexed_at is None    # not set until Day 8-9, correctly null


def test_pull_request_foreign_key_to_repository(db):
    """PR correctly references its parent repo via FK."""
    repo = db.query(Repository).filter_by(github_id=111).first()
    pr = PullRequest(repo_id=repo.id, pr_number=42, title="Fix bug", author="dev")
    db.add(pr)
    db.commit()
    db.refresh(pr)

    fetched = db.query(PullRequest).filter_by(pr_number=42).first()
    assert fetched.repo_id == repo.id
    assert fetched.status == "pending"   # default value applied correctly


def test_pull_request_rejects_invalid_repo_id(db):
    """
    FK constraint must be enforced at the DB level, not just in application code.
    repo_id=999999 does not exist — Postgres must reject this with IntegrityError.
    """
    bad_pr = PullRequest(repo_id=999999, pr_number=99)
    db.add(bad_pr)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_review_and_retrieval_log_insert(db):
    """
    Review links to a real PR. RetrievalLog is inserted with pr_id=None —
    this is the Day 10 manual-test scenario where retrieval is called
    before a real PR row exists. The nullable constraint must allow this.
    """
    pr = db.query(PullRequest).filter_by(pr_number=42).first()

    review = Review(pr_id=pr.id, risk_score=0.7, risk_label="high", issues_found=3)
    db.add(review)
    db.commit()
    db.refresh(review)

    assert review.id is not None
    assert review.total_latency_ms is None   # not populated until pipeline is wired

    # RetrievalLog with pr_id=None — must succeed per Change 2/3
    log = RetrievalLog(
        pr_id=None,
        query_text="def calculate_discount(user_id, amount):",
        retrieved_chunk_ids="uuid-1,uuid-2,uuid-3",
        scores="0.91,0.87,0.82",
        retrieval_latency_ms=43,
        token_cost=12,
    )
    db.add(log)
    db.commit()
    db.refresh(log)

    assert log.id is not None
    assert log.pr_id is None   # confirmed nullable, Change 2/3 compliant

def test_pull_request_rejects_duplicate_pr_number(db):
    repo = db.query(Repository).filter_by(github_id=111).first()
    assert repo is not None

    existing = db.query(PullRequest).filter_by(
        repo_id=repo.id,
        pr_number=42
    ).first()
    assert existing is not None

    duplicate = PullRequest(
        repo_id=repo.id,
        pr_number=42
    )
    db.add(duplicate)

    with pytest.raises(IntegrityError):
        db.commit()

    db.rollback()