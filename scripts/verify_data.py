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


def main():
    now = datetime.now()
    current = f"{now.year:04d}-{now.month:02d}"

    failures = []
    warnings = []

    print(f"検証開始: 現在月 = {current}")
    print(f"  警告閾値: {STALE_WARN} ヶ月 / 失敗閾値: {STALE_FAIL} ヶ月\n")
    print(f"{'Country':8s} {'Indicator':16s} {'Latest':10s} {'Stale(mo)':10s} Status")
    print("-" * 65)

    for code in COUNTRIES:
        path = DATA_DIR / f"{code}.json"
        if not path.exists():
            failures.append(f"{code}: file missing")
            print(f"{code:8s} {'(file missing)':16s}")
            continue
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        labels = d.get("labels", [])
        for ind in INDICATORS:
            arr = d.get(ind, [])
            if not arr or all(v is None for v in arr):
                failures.append(f"{code}.{ind}: all None or empty")
                print(f"{code:8s} {ind:16s} {'(empty)':10s} {'-':10s} FAIL")
                continue
            latest = last_valid(labels, arr)
            stale = months_between(latest, current)
            if stale > STALE_FAIL:
                status = "FAIL"
                failures.append(f"{code}.{ind}: latest={latest} (>{STALE_FAIL}mo stale)")
            elif stale > STALE_WARN:
                status = "WARN"
                warnings.append(f"{code}.{ind}: latest={latest} ({stale}mo stale)")
            else:
                status = "OK"
            print(f"{code:8s} {ind:16s} {latest:10s} {stale:<10d} {status}")

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
