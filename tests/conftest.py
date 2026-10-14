import pytest

from app import writeup


@pytest.fixture(autouse=True)
def no_writeup_call(monkeypatch):
    """The write-up is a second model call; tests that don't test it get an empty reply (#212)."""
    monkeypatch.setattr(writeup, "complete_json", lambda a, s, u, model, **k: model())
