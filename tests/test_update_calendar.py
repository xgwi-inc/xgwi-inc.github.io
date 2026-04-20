from datetime import date
from unittest.mock import patch

import pytest

from update_calendar import (
    _last_sunday,
    _nth_sunday,
    build_central_bank_events,
    get_target_months,
    to_jst,
    utc_offset,
    validate_events,
)


class TestLastSunday:
    def test_march_2026(self):
        assert _last_sunday(2026, 3) == date(2026, 3, 29)

    def test_october_2026(self):
        assert _last_sunday(2026, 10) == date(2026, 10, 25)

    def test_december_wraparound(self):
        assert _last_sunday(2026, 12) == date(2026, 12, 27)


class TestNthSunday:
    def test_first_sunday_march_2026(self):
        assert _nth_sunday(2026, 3, 1) == date(2026, 3, 1)

    def test_second_sunday_march_2026_us_dst_start(self):
        assert _nth_sunday(2026, 3, 2) == date(2026, 3, 8)

    def test_first_sunday_november_2026_us_dst_end(self):
        assert _nth_sunday(2026, 11, 1) == date(2026, 11, 1)

    def test_first_sunday_when_month_starts_on_sunday(self):
        assert _nth_sunday(2026, 2, 1) == date(2026, 2, 1)


class TestUtcOffset:
    def test_us_edt_summer(self):
        assert utc_offset("us", date(2026, 7, 15)) == -4

    def test_us_est_winter(self):
        assert utc_offset("us", date(2026, 1, 15)) == -5

    def test_us_edt_starts_on_second_sunday_of_march(self):
        assert utc_offset("us", date(2026, 3, 8)) == -4
        assert utc_offset("us", date(2026, 3, 7)) == -5

    def test_us_est_returns_on_first_sunday_of_november(self):
        assert utc_offset("us", date(2026, 11, 1)) == -5
        assert utc_offset("us", date(2026, 10, 31)) == -4

    def test_eu_cest_summer(self):
        assert utc_offset("eu", date(2026, 7, 15)) == 2

    def test_eu_cet_winter(self):
        assert utc_offset("eu", date(2026, 1, 15)) == 1

    def test_gb_bst_summer(self):
        assert utc_offset("gb", date(2026, 7, 15)) == 1

    def test_gb_gmt_winter(self):
        assert utc_offset("gb", date(2026, 1, 15)) == 0

    def test_au_aedt_summer_january(self):
        assert utc_offset("au", date(2026, 1, 15)) == 11

    def test_au_aest_winter_july(self):
        assert utc_offset("au", date(2026, 7, 15)) == 10

    def test_jp_always_nine(self):
        assert utc_offset("jp", date(2026, 1, 15)) == 9
        assert utc_offset("jp", date(2026, 7, 15)) == 9

    def test_ca_matches_us(self):
        assert utc_offset("ca", date(2026, 7, 15)) == -4
        assert utc_offset("ca", date(2026, 1, 15)) == -5

    def test_unknown_region_raises(self):
        with pytest.raises(ValueError):
            utc_offset("mars", date(2026, 1, 1))


class TestToJst:
    def test_fomc_afternoon_edt_crosses_midnight(self):
        jst_time, date_off = to_jst("14:00", "us", date(2026, 7, 29))
        assert jst_time == "03:00"
        assert date_off == 1

    def test_fomc_afternoon_est_crosses_midnight(self):
        jst_time, date_off = to_jst("14:00", "us", date(2026, 1, 28))
        assert jst_time == "04:00"
        assert date_off == 1

    def test_ecb_afternoon_cest(self):
        jst_time, date_off = to_jst("14:15", "eu", date(2026, 7, 23))
        assert jst_time == "21:15"
        assert date_off == 0

    def test_boj_noon_jst_no_shift(self):
        jst_time, date_off = to_jst("12:00", "jp", date(2026, 3, 19))
        assert jst_time == "12:00"
        assert date_off == 0

    def test_rba_afternoon_aedt(self):
        jst_time, date_off = to_jst("14:30", "au", date(2026, 2, 3))
        assert jst_time == "12:30"
        assert date_off == 0

    def test_boe_bst_noon(self):
        jst_time, date_off = to_jst("12:00", "gb", date(2026, 7, 30))
        assert jst_time == "20:00"
        assert date_off == 0


class TestGetTargetMonths:
    def test_returns_three_months_by_default(self):
        with patch("update_calendar.datetime") as mock_dt:
            mock_dt.now.return_value.year = 2026
            mock_dt.now.return_value.month = 4
            assert get_target_months() == ["2026-04", "2026-05", "2026-06"]

    def test_wraps_across_year_boundary(self):
        with patch("update_calendar.datetime") as mock_dt:
            mock_dt.now.return_value.year = 2026
            mock_dt.now.return_value.month = 11
            assert get_target_months(3) == ["2026-11", "2026-12", "2027-01"]

    def test_custom_count(self):
        with patch("update_calendar.datetime") as mock_dt:
            mock_dt.now.return_value.year = 2026
            mock_dt.now.return_value.month = 1
            assert get_target_months(1) == ["2026-01"]


class TestBuildCentralBankEvents:
    def test_filters_to_target_months(self):
        events = build_central_bank_events(["2026-04"])
        months = {e["date"][:7] for e in events}
        # JST変換で月が前後にズレることはあり得るが、少なくとも元のローカル月が4月のイベントのみ
        for e in events:
            assert e["date"][:7] in {"2026-04", "2026-05"}
        assert events, "should produce at least one event for April 2026"

    def test_empty_target_months_yields_no_events(self):
        assert build_central_bank_events([]) == []

    def test_events_have_required_fields(self):
        events = build_central_bank_events(["2026-06"])
        for e in events:
            assert set(e.keys()) >= {
                "date", "time", "country", "flag", "type", "label", "source"
            }
            assert e["source"] == "official"
            assert e["type"] == "rate"

    def test_boj_not_shifted(self):
        # 日銀は region=jp なので date_off=0 のはず
        events = build_central_bank_events(["2026-03"])
        boj = [e for e in events if e["country"] == "jp"]
        assert boj
        assert all(e["date"] == "2026-03-19" for e in boj)


class TestValidateEvents:
    def test_no_warnings_for_clean_events(self):
        events = [
            {"date": "2026-04-30", "time": "21:15", "country": "eu", "type": "rate", "label": "ECB"},
            {"date": "2026-06-17", "time": "03:00", "country": "us", "type": "rate", "label": "FOMC"},
        ]
        assert validate_events(events) == []

    def test_duplicate_detected(self):
        events = [
            {"date": "2026-04-30", "time": "12:00", "country": "jp", "type": "rate", "label": "A"},
            {"date": "2026-04-30", "time": "12:00", "country": "jp", "type": "rate", "label": "B"},
        ]
        warnings = validate_events(events)
        assert any("[重複]" in w for w in warnings)

    def test_distinct_countries_not_flagged_as_duplicate(self):
        events = [
            {"date": "2026-04-30", "time": "12:00", "country": "jp", "type": "rate", "label": "BoJ"},
            {"date": "2026-04-30", "time": "21:15", "country": "eu", "type": "rate", "label": "ECB"},
        ]
        assert validate_events(events) == []
