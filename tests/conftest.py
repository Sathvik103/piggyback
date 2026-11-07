import pytest

from simulator import generate_scenario


@pytest.fixture(scope="session")
def scenario():
    return generate_scenario(seed=42)
