import pytest

from scanner.config import load_config
from scanner.filters import Filters


@pytest.fixture(scope="session")
def filters():
    return Filters.from_config(load_config())


@pytest.fixture(autouse=True)
def _no_board_retry_pause(monkeypatch):
    from scanner.sources import watchlist
    monkeypatch.setattr(watchlist, "RETRY_PAUSE", 0)
