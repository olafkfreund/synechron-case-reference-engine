import pytest

from app import review, writeup


@pytest.fixture(autouse=True)
def no_writeup_call(monkeypatch):
    """The write-up is a second model call; tests that don't test it get an empty reply (#212)."""
    monkeypatch.setattr(writeup, "complete_json", lambda a, s, u, model, **k: model())


@pytest.fixture(autouse=True)
def fresh_staff_tab():
    """staff_uploads() caches for 60 s; every test starts without it."""
    review._staff_seen = (0.0, False)
