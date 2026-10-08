import pytest

from tagkit import normalize_tags
from tagkit.errors import InvalidTagInput


def test_basic():
    assert normalize_tags("Red, Blue") == ["red", "blue"]


def test_dedup():
    assert normalize_tags(" red,RED,, blue ,red") == ["red", "blue"]


def test_empty():
    assert normalize_tags(" , , ") == []


def test_type():
    assert issubclass(InvalidTagInput, TypeError)
    with pytest.raises(InvalidTagInput):
        normalize_tags(None)
