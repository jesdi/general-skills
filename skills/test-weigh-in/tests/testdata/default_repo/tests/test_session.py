import pytest
from demo import board, config
@pytest.mark.e2e
def test_session_0():
    assert board.render(0) == "0"
    assert config.load() == {}
@pytest.mark.e2e
def test_session_1():
    assert board.render(1) == "1"
    assert config.load() == {}
def test_session_2():
    assert board.render(2) == "2"
    assert config.load() == {}
def test_session_3():
    assert board.render(3) == "3"
    assert config.load() == {}
def test_session_4():
    assert board.render(4) == "4"
    assert config.load() == {}
