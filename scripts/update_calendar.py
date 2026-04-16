#!/usr/bin/env python3
"""
経済指標カレンダー生成スクリプト
実行月から3ヶ月分の発表予定を data/calendar.json に出力する。

データソース:
  - US CPI/PPI/雇用統計: FRED API (動的)
  - 中央銀行 金利発表: 公式スケジュール (年次更新)
  - その他統計: data/release_dates.json (年次更新)
"""

import json
import os
import re
import ssl
from datetime import datetime, date, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR.parent / "data"
UA = "Mozilla/5.0 (economic-calendar-updater)"

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  DST / タイムゾーン
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _last_sunday(year, month):
    last_day = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return last_day - timedelta(days=(last_day.weekday() + 1) % 7)

def _nth_sunday(year, month, n):
    first = date(year, month, 1)
    first_sun = first + timedelta(days=(6 - first.weekday()) % 7)
    return first_sun + timedelta(weeks=n - 1)

def utc_offset(region, dt):
    """日付に応じたUTCオフセット(時間)を返す"""
    y = dt.year
    if region in ("us", "ca"):
        # EDT: 3月第2日曜 〜 11月第1日曜
        return -4 if _nth_sunday(y, 3, 2) <= dt < _nth_sunday(y, 11, 1) else -5
    elif region == "eu":
        # CEST: 3月最終日曜 〜 10月最終日曜
        return 2 if _last_sunday(y, 3) <= dt < _last_sunday(y, 10) else 1
    elif region == "gb":
        # BST: 3月最終日曜 〜 10月最終日曜
        return 1 if _last_sunday(y, 3) <= dt < _last_sunday(y, 10) else 0
    elif region == "au":
        # AEDT: 10月第1日曜 〜 4月第1日曜
        if dt < _nth_sunday(y, 4, 1) or dt >= _nth_sunday(y, 10, 1):
            return 11  # AEDT
        return 10  # AEST
    elif region == "jp":
        return 9
    raise ValueError(f"Unknown region: {region}")

def to_jst(local_time, region, dt):
    """ローカル時刻(HH:MM) → JST時刻 と 日付オフセット を返す"""
    h, m = map(int, local_time.split(":"))
    delta = 9 - utc_offset(region, dt)
    jst_h = h + delta
    date_off = 0
    while jst_h >= 24:
        jst_h -= 24
        date_off += 1
    while jst_h < 0:
        jst_h += 24
        date_off -= 1
    return f"{jst_h:02d}:{m:02d}", date_off


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  FRED API (US CPI / PPI / Employment)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FRED_RELEASES = [
    {"releaseId": 10, "type": "cpi",   "label": "CPI"},
    {"releaseId": 46, "type": "ppi",   "label": "PPI"},
    {"releaseId": 50, "type": "unemp", "label": "雇用統計（失業率）"},
]

def fetch_fred_release_dates(target_months):
    """FRED release/dates API から米国統計の発表日を取得"""
    if not FRED_API_KEY:
        print("  FRED_API_KEY が未設定、スキップ")
        return []

    events = []
    for rel in FRED_RELEASES:
        url = (
            f"https://api.stlouisfed.org/fred/release/dates"
            f"?release_id={rel['releaseId']}&api_key={FRED_API_KEY}"
            f"&file_type=json&include_release_dates_with_no_data=true"
            f"&sort_order=desc&limit=24"
        )
        try:
            with urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read())
            for item in data.get("release_dates", []):
                d = item["date"]  # "YYYY-MM-DD"
                ym = d[:7]
                if ym in target_months:
                    dt = date.fromisoformat(d)
                    jst_time, date_off = to_jst("08:30", "us", dt)
                    jst_date = dt + timedelta(days=date_off)
                    events.append({
                        "date": jst_date.isoformat(),
                        "time": jst_time,
                        "country": "us",
                        "flag": "🇺🇸",
                        "type": rel["type"],
                        "label": rel["label"],
                        "source": "FRED",
                    })
            print(f"    {rel['label']}: {len([e for e in events if e['type']==rel['type']])} 件")
        except Exception as e:
            print(f"    {rel['label']}: [エラー] {e}")
    return events


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  中央銀行 金利発表日 (公式スケジュール, 年次更新)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 各中央銀行の2026年スケジュール
# 日付は発表日(会合2日目)、時刻はローカル時間
CENTRAL_BANKS = [
    # FOMC: 14:00 ET (日本時間は翌日03:00 or 04:00)
    {
        "dates": ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
                  "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"],
        "localTime": "14:00", "region": "us",
        "country": "us", "flag": "🇺🇸", "type": "rate",
        "label": "FOMC 政策金利発表",
    },
    # ECB: 14:15 CEST/CET
    {
        "dates": ["2026-04-30", "2026-06-11", "2026-07-23",
                  "2026-09-10", "2026-10-29", "2026-12-17"],
        "localTime": "14:15", "region": "eu",
        "country": "eu", "flag": "🇪🇺", "type": "rate",
        "label": "ECB 政策金利発表",
    },
    # BoE MPC: 12:00 BST/GMT
    {
        "dates": ["2026-02-05", "2026-03-19", "2026-04-30", "2026-06-18",
                  "2026-07-30", "2026-09-17", "2026-11-05", "2026-12-17"],
        "localTime": "12:00", "region": "gb",
        "country": "gb", "flag": "🇬🇧", "type": "rate",
        "label": "BoE 政策金利発表",
    },
    # BoC: 09:45 ET
    {
        "dates": ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-10",
                  "2026-07-15", "2026-09-02", "2026-10-28", "2026-12-09"],
        "localTime": "09:45", "region": "ca",
        "country": "ca", "flag": "🇨🇦", "type": "rate",
        "label": "BoC 政策金利発表",
    },
    # RBA: 14:30 AEST (会合2日目)
    {
        "dates": ["2026-02-03", "2026-03-17", "2026-05-05", "2026-06-16",
                  "2026-08-11", "2026-09-29", "2026-11-03", "2026-12-08"],
        "localTime": "14:30", "region": "au",
        "country": "au", "flag": "🇦🇺", "type": "rate",
        "label": "RBA 政策金利発表",
    },
    # BoJ MPM: ~12:00 JST (会合2日目)
    {
        "dates": ["2026-01-23", "2026-03-19", "2026-04-28", "2026-06-16",
                  "2026-07-31", "2026-09-18", "2026-10-30", "2026-12-18"],
        "localTime": "12:00", "region": "jp",
        "country": "jp", "flag": "🇯🇵", "type": "rate",
        "label": "日銀 金融政策決定会合",
    },
]

def build_central_bank_events(target_months):
    """中央銀行の金利発表イベントを生成"""
    events = []
    for cb in CENTRAL_BANKS:
        for d_str in cb["dates"]:
            ym = d_str[:7]
            if ym not in target_months:
                continue
            dt = date.fromisoformat(d_str)
            jst_time, date_off = to_jst(cb["localTime"], cb["region"], dt)
            jst_date = dt + timedelta(days=date_off)
            # 日付が変わった場合、月が対象月か再確認
            jst_ym = jst_date.isoformat()[:7]
            if jst_ym not in target_months:
                # 元の月が対象なら含める（例: FOMC 4/29 14:00ET → 4/30 03:00 JST）
                if ym not in target_months:
                    continue
            events.append({
                "date": jst_date.isoformat(),
                "time": jst_time,
                "country": cb["country"],
                "flag": cb["flag"],
                "type": cb["type"],
                "label": cb["label"],
                "source": "official",
            })
    return events


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  統計発表日 (設定ファイルから読み込み)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def load_release_dates(target_months):
    """data/release_dates.json から統計発表日を読み込み、JST変換して返す"""
    path = DATA_DIR / "release_dates.json"
    if not path.exists():
        print(f"  {path} が見つかりません")
        return []

    with open(path, encoding="utf-8") as f:
        raw_events = json.load(f)

    events = []
    for ev in raw_events:
        d_str = ev["date"]
        ym = d_str[:7]
        if ym not in target_months:
            continue
        dt = date.fromisoformat(d_str)
        jst_time, date_off = to_jst(ev["localTime"], ev["region"], dt)
        jst_date = dt + timedelta(days=date_off)
        events.append({
            "date": jst_date.isoformat(),
            "time": jst_time,
            "country": ev["country"],
            "flag": ev["flag"],
            "type": ev["type"],
            "label": ev["label"],
            "source": ev.get("source", "schedule"),
        })
    return events


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  バリデーション
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def validate_events(events):
    """イベントのバリデーション（警告のみ、削除はしない）"""
    warnings = []
    for ev in events:
        dt = date.fromisoformat(ev["date"])
        dow = dt.weekday()  # 0=Mon, 5=Sat, 6=Sun
        # JST変換後の日付が週末になることはある（例: FOMC 金曜14:00ET → 土曜03:00 JST）
        # ただし元のローカル日付が週末の場合は警告
        # ここではJST日付のチェックのみ
        h = int(ev["time"].split(":")[0])
        if h < 0 or h > 23:
            warnings.append(f"  [時刻異常] {ev['date']} {ev['time']} {ev['label']}")

    # 重複チェック
    seen = set()
    for ev in events:
        key = (ev["date"], ev["country"], ev["type"])
        if key in seen:
            warnings.append(f"  [重複] {ev['date']} {ev['country']} {ev['type']} {ev['label']}")
        seen.add(key)

    return warnings


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  メイン
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_target_months(count=3):
    """実行月からcount ヶ月分のYYYY-MMリストを返す"""
    now = datetime.now()
    months = []
    y, m = now.year, now.month
    for _ in range(count):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return months


def main():
    print("=" * 50)
    print("経済指標カレンダー生成")
    print(f"実行日時: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    target_months = get_target_months(3)
    print(f"対象月: {', '.join(target_months)}")

    all_events = []

    # 1. FRED API (US 統計)
    print("\n📊 US 統計指標 (FRED API)")
    fred_events = fetch_fred_release_dates(target_months)
    all_events.extend(fred_events)
    print(f"  合計: {len(fred_events)} 件")

    # 2. 中央銀行 金利発表
    print("\n🏦 中央銀行 金利発表")
    cb_events = build_central_bank_events(target_months)
    all_events.extend(cb_events)
    print(f"  合計: {len(cb_events)} 件")

    # 3. その他統計 (設定ファイル)
    print("\n📅 統計発表日 (release_dates.json)")
    stat_events = load_release_dates(target_months)
    all_events.extend(stat_events)
    print(f"  合計: {len(stat_events)} 件")

    # 4. バリデーション
    print("\n✅ バリデーション")
    warnings = validate_events(all_events)
    if warnings:
        for w in warnings:
            print(w)
    else:
        print("  問題なし")

    # 5. ソート
    all_events.sort(key=lambda e: (e["date"], e["time"]))

    # 6. 出力
    output = {
        "generated": datetime.now().strftime("%Y-%m-%d"),
        "months": target_months,
        "events": all_events,
    }
    out_path = DATA_DIR / "calendar.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n📁 {out_path} に出力 ({len(all_events)} 件)")

    # 月別サマリー
    for ym in target_months:
        month_events = [e for e in all_events if e["date"].startswith(ym)]
        print(f"  {ym}: {len(month_events)} 件")

    print("\n" + "=" * 50)
    print("完了！")
    print("=" * 50)


if __name__ == "__main__":
    main()
