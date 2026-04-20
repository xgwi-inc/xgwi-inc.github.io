import json
from pathlib import Path

import pytest

from verify_data import classify, last_valid, months_between, verify


class TestMonthsBetween:
    def test_same_month(self):
        assert months_between("2026-04", "2026-04") == 0

    def test_one_month_forward(self):
        assert months_between("2026-03", "2026-04") == 1

    def test_year_boundary(self):
        assert months_between("2025-12", "2026-01") == 1

    def test_multi_year(self):
        assert months_between("2024-06", "2026-04") == 22

    def test_negative_when_latest_is_future(self):
        assert months_between("2026-05", "2026-04") == -1


class TestLastValid:
    def test_returns_last_non_none(self):
        labels = ["2026-01", "2026-02", "2026-03"]
        arr = [1.0, 2.0, 3.0]
        assert last_valid(labels, arr) == "2026-03"

    def test_skips_trailing_none(self):
        labels = ["2026-01", "2026-02", "2026-03"]
        arr = [1.0, 2.0, None]
        assert last_valid(labels, arr) == "2026-02"

    def test_skips_multiple_trailing_none(self):
        labels = ["2026-01", "2026-02", "2026-03", "2026-04"]
        arr = [1.0, None, None, None]
        assert last_valid(labels, arr) == "2026-01"

    def test_all_none_returns_none(self):
        labels = ["2026-01", "2026-02"]
        arr = [None, None]
        assert last_valid(labels, arr) is None

    def test_empty_returns_none(self):
        assert last_valid([], []) is None


class TestClassify:
    def test_zero_is_ok(self):
        assert classify(0) == "OK"

    def test_below_warn_is_ok(self):
        assert classify(3, warn=3, fail=6) == "OK"

    def test_above_warn_is_warn(self):
        assert classify(4, warn=3, fail=6) == "WARN"

    def test_at_fail_is_warn(self):
        assert classify(6, warn=3, fail=6) == "WARN"

    def test_above_fail_is_fail(self):
        assert classify(7, warn=3, fail=6) == "FAIL"

    def test_far_above_fail(self):
        assert classify(24, warn=3, fail=6) == "FAIL"


def _write_json(path: Path, labels, **indicators):
    path.write_text(
        json.dumps({"labels": labels, **indicators}), encoding="utf-8"
    )


class TestVerify:
    def test_all_fresh_returns_ok(self, tmp_path):
        _write_json(
            tmp_path / "us.json",
            ["2026-03", "2026-04"],
            interestRate=[5.0, 5.0],
            cpi=[3.0, 3.1],
            unemployment=[4.0, 4.1],
        )
        rows, failures, warnings = verify(
            tmp_path, "2026-04", countries=["us"],
            indicators=["interestRate", "cpi", "unemployment"],
        )
        assert failures == []
        assert warnings == []
        assert all(r["status"] == "OK" for r in rows)
        assert len(rows) == 3

    def test_missing_file_is_failure(self, tmp_path):
        rows, failures, warnings = verify(
            tmp_path, "2026-04", countries=["us"], indicators=["cpi"],
        )
        assert len(failures) == 1
        assert "us" in failures[0]
        assert rows[0]["status"] == "FAIL"
        assert rows[0]["indicator"] is None

    def test_empty_indicator_array_is_failure(self, tmp_path):
        _write_json(tmp_path / "us.json", ["2026-04"], cpi=[])
        rows, failures, _ = verify(
            tmp_path, "2026-04", countries=["us"], indicators=["cpi"],
        )
        assert len(failures) == 1
        assert "all None or empty" in failures[0]
        assert rows[0]["status"] == "FAIL"

    def test_all_none_indicator_is_failure(self, tmp_path):
        _write_json(tmp_path / "us.json", ["2026-03", "2026-04"], cpi=[None, None])
        rows, failures, _ = verify(
            tmp_path, "2026-04", countries=["us"], indicators=["cpi"],
        )
        assert len(failures) == 1
        assert rows[0]["status"] == "FAIL"

    def test_stale_warn_threshold(self, tmp_path):
        _write_json(
            tmp_path / "us.json",
            ["2025-11", "2025-12"],
            cpi=[3.0, 3.1],
        )
        rows, failures, warnings = verify(
            tmp_path, "2026-04", countries=["us"], indicators=["cpi"],
        )
        assert failures == []
        assert len(warnings) == 1
        assert rows[0]["status"] == "WARN"
        assert rows[0]["stale"] == 4

    def test_stale_fail_threshold(self, tmp_path):
        _write_json(
            tmp_path / "us.json",
            ["2025-06"],
            cpi=[3.0],
        )
        rows, failures, _ = verify(
            tmp_path, "2026-04", countries=["us"], indicators=["cpi"],
        )
        assert len(failures) == 1
        assert rows[0]["status"] == "FAIL"
        assert rows[0]["stale"] == 10

    def test_uses_last_non_none_for_staleness(self, tmp_path):
        _write_json(
            tmp_path / "us.json",
            ["2026-02", "2026-03", "2026-04"],
            cpi=[3.0, 3.1, None],
        )
        rows, _, _ = verify(
            tmp_path, "2026-04", countries=["us"], indicators=["cpi"],
        )
        assert rows[0]["latest"] == "2026-03"
        assert rows[0]["stale"] == 1
        assert rows[0]["status"] == "OK"

    def test_mixed_countries(self, tmp_path):
        _write_json(tmp_path / "us.json", ["2026-04"], cpi=[3.1])
        _write_json(tmp_path / "ca.json", ["2025-06"], cpi=[2.0])
        rows, failures, warnings = verify(
            tmp_path, "2026-04", countries=["us", "ca"], indicators=["cpi"],
        )
        statuses = {r["country"]: r["status"] for r in rows}
        assert statuses == {"us": "OK", "ca": "FAIL"}
        assert len(failures) == 1
        assert "ca.cpi" in failures[0]
