import pytest

from scanner.config import load_config
from scanner.filters import Filters


@pytest.fixture(scope="session")
def filters():
    return Filters.from_config(load_config())
