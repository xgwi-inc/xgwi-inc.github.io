#!/usr/bin/env python3
"""Verify that each data/<code>.json has plausibly fresh values for all indicators.

Rules:
  * Every indicator must have at least one non-None value.
  * The latest non-None period per indicator must be within STALE_FAIL months
    of the current month. Anything older is a failure (upstream broken or
    an API changed format).
  * STALE_WARN..STALE_FAIL inclusive prints a warning but does not fail.

Exit code: 0 = all green, 1 = any failures.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
COUNTRIES = ["us", "ca", "eu", "gb", "au", "jp"]
INDICATORS = ["interestRate", "cpi", "unemployment"]

STALE_WARN = 3   # months
STALE_FAIL = 6   # months


def months_between(a, b):
    """a, b are YYYY-MM. Returns b - a in months."""
    ya, ma = int(a[:4]), int(a[5:7])
    yb, mb = int(b[:4]), int(b[5:7])
    return (yb - ya) * 12 + (mb - ma)


def last_valid(labels, arr):
    for i in range(len(arr) - 1, -1, -1):
        if arr[i] is not None:
            return labels[i]
    return None


def classify(stale, warn=STALE_WARN, fail=STALE_FAIL):
    """Return 'OK' | 'WARN' | 'FAIL' for a stale-in-months value."""
    if stale > fail:
        return "FAIL"
    if stale > warn:
        return "WARN"
    return "OK"


def verify(data_dir, current, countries=COUNTRIES, indicators=INDICATORS):
    """Core verification logic (no I/O side effects on exit).

    Returns: (rows, failures, warnings)
      rows: list of dicts {country, indicator, latest, stale, status}
      failures / warnings: list of str descriptions
    """
    rows = []
    failures = []
    warnings = []
    data_dir = Path(data_dir)
    for code in countries:
        path = data_dir / f"{code}.json"
        if not path.exists():
            failures.append(f"{code}: file missing")
            rows.append({"country": code, "indicator": None, "latest": None,
                         "stale": None, "status": "FAIL"})
            continue
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        labels = d.get("labels", [])
        for ind in indicators:
            arr = d.get(ind, [])
            if not arr or all(v is None for v in arr):
                failures.append(f"{code}.{ind}: all None or empty")
                rows.append({"country": code, "indicator": ind, "latest": None,
                             "stale": None, "status": "FAIL"})
                continue
            latest = last_valid(labels, arr)
            stale = months_between(latest, current)
            status = classify(stale)
            if status == "FAIL":
                failures.append(f"{code}.{ind}: latest={latest} (>{STALE_FAIL}mo stale)")
            elif status == "WARN":
                warnings.append(f"{code}.{ind}: latest={latest} ({stale}mo stale)")
            rows.append({"country": code, "indicator": ind, "latest": latest,
                         "stale": stale, "status": status})
    return rows, failures, warnings


def main():
    now = datetime.now()
    current = f"{now.year:04d}-{now.month:02d}"

    print(f"検証開始: 現在月 = {current}")
    print(f"  警告閾値: {STALE_WARN} ヶ月 / 失敗閾値: {STALE_FAIL} ヶ月\n")
    print(f"{'Country':8s} {'Indicator':16s} {'Latest':10s} {'Stale(mo)':10s} Status")
    print("-" * 65)

    rows, failures, warnings = verify(DATA_DIR, current)
    for r in rows:
        ind = r["indicator"] or "(file missing)"
        latest = r["latest"] or "(empty)"
        stale = "-" if r["stale"] is None else str(r["stale"])
        print(f"{r['country']:8s} {ind:16s} {latest:10s} {stale:<10s} {r['status']}")

    print()
    if warnings:
        print("⚠️  Warnings:")
        for w in warnings:
            print(f"  - {w}")
    if failures:
        print("❌ Failures:")
        for fail in failures:
            print(f"  - {fail}")
        sys.exit(1)
    print("✅ 全指標OK")


if __name__ == "__main__":
    main()
