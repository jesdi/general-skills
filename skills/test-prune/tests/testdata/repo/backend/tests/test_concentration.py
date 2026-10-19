"""Concentration (HHI) tests."""
import pytest

from services.concentration import herfindahl


def test_hhi_reference():
    assert herfindahl([0.5, 0.5]) == pytest.approx(0.5)
    assert herfindahl([1.0]) == 1.0


def test_hhi_case_01():
    assert herfindahl([1 / 2] * 2) == pytest.approx(1 / 2)


def test_hhi_case_02():
    assert herfindahl([1 / 3] * 3) == pytest.approx(1 / 3)


def test_hhi_case_03():
    assert herfindahl([1 / 4] * 4) == pytest.approx(1 / 4)


def test_hhi_case_04():
    assert herfindahl([1 / 5] * 5) == pytest.approx(1 / 5)


def test_hhi_case_05():
    assert herfindahl([1 / 6] * 6) == pytest.approx(1 / 6)


def test_hhi_case_06():
    assert herfindahl([1 / 7] * 7) == pytest.approx(1 / 7)


def test_hhi_case_07():
    assert herfindahl([1 / 8] * 8) == pytest.approx(1 / 8)


def test_hhi_case_08():
    assert herfindahl([1 / 9] * 9) == pytest.approx(1 / 9)


def test_hhi_case_09():
    assert herfindahl([1 / 10] * 10) == pytest.approx(1 / 10)


def test_hhi_case_10():
    assert herfindahl([1 / 11] * 11) == pytest.approx(1 / 11)


def test_hhi_case_11():
    assert herfindahl([1 / 12] * 12) == pytest.approx(1 / 12)


def test_hhi_case_12():
    assert herfindahl([1 / 13] * 13) == pytest.approx(1 / 13)


def test_hhi_case_13():
    assert herfindahl([1 / 14] * 14) == pytest.approx(1 / 14)


def test_hhi_case_14():
    assert herfindahl([1 / 15] * 15) == pytest.approx(1 / 15)


def test_hhi_case_15():
    assert herfindahl([1 / 16] * 16) == pytest.approx(1 / 16)


def test_hhi_case_16():
    assert herfindahl([1 / 17] * 17) == pytest.approx(1 / 17)
