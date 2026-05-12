import pytest
from worker.app.agent import agent as _agent


@pytest.fixture(scope="module")
def agent():
    return _agent
