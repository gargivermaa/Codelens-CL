"""Shared test configuration."""
import pytest

from app.config import settings
from app.core import embedder


@pytest.fixture(autouse=True)
def _no_gemini_throttle(monkeypatch):
    # Client-side pacing protects the real free-tier quota; tests use fakes and
    # opt in explicitly when they test the pacing itself.
    monkeypatch.setattr(settings, "GEMINI_MIN_REQUEST_INTERVAL_S", 0.0)
    monkeypatch.setattr(settings, "GEMINI_EMBED_INPUTS_PER_MINUTE", 0)
    embedder._input_window.clear()
    monkeypatch.setattr(embedder, "_last_request_at", 0.0)
