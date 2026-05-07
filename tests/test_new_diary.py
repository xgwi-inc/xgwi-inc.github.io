import json

import pytest

from new_diary import (
    empty_diary,
    format_pips,
    load_diary,
    recent_symbols,
    save_diary,
    summarize,
    upsert_entry,
)


class TestSummarize:
    def test_all_wins(self):
        assert summarize([
            {"symbol": "USDJPY", "pnl_pips": 10},
            {"symbol": "EURUSD", "pnl_pips": 5.5},
        ]) == (2, 0, 15.5)

    def test_all_losses(self):
        assert summarize([
            {"symbol": "GBPUSD", "pnl_pips": -10.6},
        ]) == (0, 1, -10.6)

    def test_mixed(self):
        wins, losses, total = summarize([
            {"symbol": "A", "pnl_pips": 5},
            {"symbol": "B", "pnl_pips": -3},
            {"symbol": "C", "pnl_pips": 0},
        ])
        assert (wins, losses, total) == (1, 1, 2)

    def test_zero_is_not_counted_as_win_or_loss(self):
        assert summarize([{"symbol": "X", "pnl_pips": 0}]) == (0, 0, 0)

    def test_empty_trades(self):
        assert summarize([]) == (0, 0, 0)

    def test_rounds_to_one_decimal(self):
        # 0.1 + 0.2 = 0.30000000000000004 を吸収
        wins, losses, total = summarize([
            {"symbol": "A", "pnl_pips": 0.1},
            {"symbol": "B", "pnl_pips": 0.2},
        ])
        assert total == 0.3


class TestFormatPips:
    def test_positive_has_plus(self):
        assert format_pips(10.6) == "+10.6pips"

    def test_negative_has_minus(self):
        assert format_pips(-10.6) == "-10.6pips"

    def test_zero_has_no_sign(self):
        assert format_pips(0) == "0pips"

    def test_integer_no_trailing_zero(self):
        assert format_pips(15) == "+15pips"


class TestUpsertEntry:
    def test_appends_new_date(self):
        data = empty_diary()
        upsert_entry(data, {"date": "2026-05-07", "trades": [
            {"symbol": "GBPUSD", "pnl_pips": -10.6}
        ]})
        assert len(data["entries"]) == 1
        assert data["entries"][0]["date"] == "2026-05-07"

    def test_merge_appends_trades_to_existing_date(self):
        data = empty_diary()
        upsert_entry(data, {"date": "2026-05-07", "trades": [
            {"symbol": "GBPUSD", "pnl_pips": -10.6}
        ]})
        upsert_entry(data, {"date": "2026-05-07", "trades": [
            {"symbol": "USDJPY", "pnl_pips": 5}
        ]}, mode="merge")
        assert len(data["entries"]) == 1
        assert len(data["entries"][0]["trades"]) == 2

    def test_replace_overwrites_existing_date(self):
        data = empty_diary()
        upsert_entry(data, {"date": "2026-05-07", "trades": [
            {"symbol": "GBPUSD", "pnl_pips": -10.6},
            {"symbol": "USDJPY", "pnl_pips": 5},
        ]})
        upsert_entry(data, {"date": "2026-05-07", "trades": [
            {"symbol": "EURUSD", "pnl_pips": 1.2}
        ]}, mode="replace")
        assert len(data["entries"]) == 1
        assert data["entries"][0]["trades"] == [
            {"symbol": "EURUSD", "pnl_pips": 1.2}
        ]

    def test_keeps_descending_date_order(self):
        data = empty_diary()
        upsert_entry(data, {"date": "2026-05-05", "trades": [{"symbol": "A", "pnl_pips": 1}]})
        upsert_entry(data, {"date": "2026-05-07", "trades": [{"symbol": "B", "pnl_pips": 2}]})
        upsert_entry(data, {"date": "2026-05-06", "trades": [{"symbol": "C", "pnl_pips": 3}]})
        dates = [e["date"] for e in data["entries"]]
        assert dates == ["2026-05-07", "2026-05-06", "2026-05-05"]


class TestRecentSymbols:
    def test_empty_data_yields_empty_list(self):
        assert recent_symbols(empty_diary()) == []

    def test_dedupes_preserving_recency_order(self):
        # entries は新しい順前提
        data = {
            "entries": [
                {"date": "2026-05-07", "trades": [
                    {"symbol": "USDJPY", "pnl_pips": 1},
                    {"symbol": "GBPUSD", "pnl_pips": -2},
                ]},
                {"date": "2026-05-06", "trades": [
                    {"symbol": "USDJPY", "pnl_pips": 3},
                    {"symbol": "EURUSD", "pnl_pips": 4},
                ]},
            ]
        }
        assert recent_symbols(data) == ["USDJPY", "GBPUSD", "EURUSD"]

    def test_caps_at_n(self):
        data = {
            "entries": [
                {"date": "2026-05-07", "trades": [
                    {"symbol": s, "pnl_pips": 0} for s in ["A", "B", "C", "D", "E"]
                ]},
            ]
        }
        assert recent_symbols(data, n=3) == ["A", "B", "C"]


class TestLoadSaveRoundTrip:
    def test_load_missing_file_returns_empty(self, tmp_path):
        assert load_diary(tmp_path / "nope.json") == empty_diary()

    def test_round_trip(self, tmp_path):
        path = tmp_path / "diary.json"
        data = empty_diary()
        upsert_entry(data, {"date": "2026-05-07", "trades": [
            {"symbol": "GBPUSD", "pnl_pips": -10.6}
        ]})
        save_diary(path, data)

        loaded = load_diary(path)
        assert loaded["count"] == 1
        assert loaded["lastUpdated"] is not None
        assert loaded["entries"][0]["trades"][0]["symbol"] == "GBPUSD"

    def test_save_writes_utf8_no_ascii_escape(self, tmp_path):
        path = tmp_path / "diary.json"
        data = empty_diary()
        upsert_entry(data, {"date": "2026-05-07", "trades": [
            {"symbol": "ドル円", "pnl_pips": 5}
        ]})
        save_diary(path, data)
        text = path.read_text(encoding="utf-8")
        assert "ドル円" in text  # ド などにエスケープされていない

    def test_save_updates_count_to_match_entries(self, tmp_path):
        path = tmp_path / "diary.json"
        data = {"lastUpdated": None, "count": 999, "entries": [
            {"date": "2026-05-07", "trades": [{"symbol": "A", "pnl_pips": 1}]},
        ]}
        save_diary(path, data)
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["count"] == 1


class TestExistingDataFile:
    def test_data_diary_json_is_valid(self):
        from pathlib import Path
        path = Path(__file__).parent.parent / "data" / "diary.json"
        data = load_diary(path)
        assert "entries" in data
        assert isinstance(data["entries"], list)
