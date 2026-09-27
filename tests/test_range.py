import os
os.environ["BOT_TOKEN"] = "test"
os.environ["DATABASE_URL"] = "postgresql://test:test@localhost/test"

from bot.main import parse_range


def test_single():
    assert parse_range("10") == (10, 10)


def test_range():
    assert parse_range("1 - 10") == (1, 10)


def test_invalid():
    assert parse_range("10-1") is None
