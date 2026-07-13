from datetime import date
from unittest.mock import patch

from app.domain.age import is_adult


class TestIsAdult:
    def test_adult_true(self):
        with patch("app.domain.age.date") as d:
            d.today.return_value = date(2026, 7, 13)
            assert is_adult("13/07/2008") is True   # exatamente 18
            assert is_adult("01/01/1990") is True

    def test_minor_false(self):
        with patch("app.domain.age.date") as d:
            d.today.return_value = date(2026, 7, 13)
            assert is_adult("14/07/2008") is False  # 18 só amanhã
            assert is_adult("01/01/2015") is False

    def test_missing_or_invalid_is_false(self):
        assert is_adult(None) is False
        assert is_adult("") is False
        assert is_adult("2008-07-13") is False       # ISO não aceito
        assert is_adult("banana") is False
