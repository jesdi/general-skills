import pytest
from demo import board, config
def test_report_0():
    assert board.render(0) == "0"
    assert config.load() == {}
def test_report_1():
    assert board.render(1) == "1"
    assert config.load() == {}
def test_report_2():
    assert board.render(2) == "2"
    assert config.load() == {}
def test_report_3():
    assert board.render(3) == "3"
    assert config.load() == {}
def test_report_4():
    assert board.render(4) == "4"
    assert config.load() == {}
