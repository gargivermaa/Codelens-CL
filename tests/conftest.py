"""Shared test configuration."""
import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def _no_gemini_throttle(monkeypatch):
    # Client-side request spacing protects the real free-tier quota; tests use fakes.
    monkeypatch.setattr(settings, "GEMINI_MIN_REQUEST_INTERVAL_S", 0.0)
