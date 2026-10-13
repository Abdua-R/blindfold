import pytest


@pytest.fixture(autouse=True)
def _blindfold_home(tmp_path, monkeypatch):
    """Keep every test's local state (session maps, the L5 cache) out of the real home folder."""
    monkeypatch.setenv("BLINDFOLD_HOME", str(tmp_path / "bfhome"))
