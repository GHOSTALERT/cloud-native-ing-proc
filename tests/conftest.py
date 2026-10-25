import fakeredis
import pytest

from imgframe.sample import synthetic_image
from imgframe.store import Store


@pytest.fixture
def image():
    return synthetic_image()


@pytest.fixture
def store():
    return Store(client=fakeredis.FakeRedis())
