#!/usr/bin/env python3
"""
経済指標データ更新スクリプト

使い方:
  export FRED_API_KEY=your_key_here
  python scripts/update_data.py

データソース:
  US     - FRED API (FEDFUNDS, CPIAUCSL, UNRATE)
  Canada - FRED API (IRSTCI01CAM156N, LRUNTTTTCAM156S) + Bank of Canada (CPI)
  EU     - ECB Data API (HICP) + Eurostat API (unemployment) + ECB (deposit rate)
  UK     - FRED API (GBRCPIALLMINMEI, LRHUTTTTGBM156S) + BoE Bank Rate (hardcoded)
  AU     - OECD API (CPI) + FRED API (unemployment) + RBA Cash Rate (hardcoded)
  JP     - FRED API (LRHUTTTTJPM156S) + BoJ Policy Rate (hardcoded) + CPI (未対応)
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import HTTPError
from xml.etree import ElementTree

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR.parent / "data"
START_DATE = "2016-01-01"
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Utility
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_url(url, accept=None):
    headers = {"User-Agent": "Mozilla/5.0 (economic-data-updater)"}
    if accept:
        headers["Accept"] = accept
    req = Request(url, headers=headers)
    with urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def fetch_json(url):
    return json.loads(fetch_url(url))


def round1(v):
    """Round to 1 decimal place."""
    return round(v, 1)


def to_label(date_str):
    """'2024-01-01' -> '2024-01'"""
    return date_str[:7]


def load_existing(code):
    path = DATA_DIR / f"{code}.json"
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_json(code, data):
    path = DATA_DIR / f"{code}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"  -> {path} を更新しました")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  FRED API
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_fred(series_id, units="lin", start=START_DATE):
    url = (
        f"https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series_id}"
        f"&api_key={FRED_API_KEY}"
        f"&file_type=json"
        f"&observation_start={start}"
        f"&units={units}"
        f"&sort_order=asc"
    )
    data = fetch_json(url)
    result = {}
    for obs in data["observations"]:
        if obs["value"] == ".":
            result[to_label(obs["date"])] = None
        else:
            result[to_label(obs["date"])] = round1(float(obs["value"]))
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  ECB Data API (EU HICP)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_ecb_hicp():
    url = (
        "https://data-api.ecb.europa.eu/service/data/"
        "ICP/M.U2.N.000000.4.ANR"
        f"?startPeriod={START_DATE[:7]}&format=csvdata"
    )
    text = fetch_url(url, accept="text/csv")
    result = {}
    for line in text.strip().split("\n")[1:]:  # skip header
        cols = line.split(",")
        period = cols[7]  # TIME_PERIOD
        value = cols[8]   # OBS_VALUE
        if period.startswith("20") and value:
            result[period] = round1(float(value))
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Eurostat API (EU unemployment)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_eurostat_unemployment():
    url = (
        "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/"
        "une_rt_m/M.SA.TOTAL.PC_ACT.T.EA20"
        f"?startPeriod={START_DATE[:7]}"
    )
    xml_text = fetch_url(url)
    result = {}
    # Parse XML for ObsDimension (period) and ObsValue
    for m in re.finditer(
        r'ObsDimension value="(\d{4}-\d{2})".*?ObsValue value="([\d.]+)"',
        xml_text,
    ):
        period, value = m.group(1), m.group(2)
        result[period] = round1(float(value))
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  ECB deposit facility rate
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_ecb_deposit_rate():
    """Fetch ECB key interest rates page and build monthly series."""
    url = "https://www.ecb.europa.eu/stats/policy_and_exchange_rates/key_ecb_interest_rates/html/index.en.html"
    html = fetch_url(url)

    month_map = {
        'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4,
        'May': 5, 'Jun': 6, 'Jul': 7, 'Aug': 8,
        'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12
    }

    # Parse table rows: cells = [year, "day Mon.", deposit_rate, mro_rate, ...]
    decisions = []
    current_year = None
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
    for tr in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', tr, re.DOTALL)
        cells = [re.sub(r'<[^>]+>', '', c).strip()
                 .replace('&nbsp;', '').replace('&#8722;', '-').replace('−', '-')
                 for c in cells]
        if len(cells) < 4:
            continue

        # First cell is year (may be empty if same year as previous row)
        if cells[0].isdigit():
            current_year = int(cells[0])
        if current_year is None or current_year < 2014:
            continue

        # Second cell is date like "11 Jun." or "10 May"
        date_match = re.match(r'(\d{1,2})\s+(\w{3})', cells[1])
        if not date_match:
            continue

        day = int(date_match.group(1))
        mon_str = date_match.group(2).rstrip('.')
        if mon_str not in month_map:
            continue
        month = month_map[mon_str]

        # Third cell is deposit facility rate
        try:
            rate = float(cells[2])
        except ValueError:
            continue

        decisions.append((current_year, month, day, rate))

    if not decisions:
        print("  [警告] ECB金利データの取得に失敗。既存データを維持します。")
        return None

    # Sort chronologically
    decisions.sort()

    # Build monthly series using rate at end of each month
    pre_rate = decisions[0][3]
    for y, m, d, r in decisions:
        if (y, m) < (2016, 1):
            pre_rate = r

    current_rate = pre_rate
    decision_map = {}
    for y, m, d, r in decisions:
        key = f"{y:04d}-{m:02d}"
        decision_map[key] = r

    result = {}
    now = datetime.now()
    y, m = 2016, 1
    while (y, m) <= (now.year, now.month):
        key = f"{y:04d}-{m:02d}"
        if key in decision_map:
            current_rate = decision_map[key]
        result[key] = round(current_rate, 2)
        m += 1
        if m > 12:
            m = 1
            y += 1

    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Bank of Canada CPI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_boc_cpi():
    """Fetch CPI YoY from Bank of Canada website."""
    url = "https://www.bankofcanada.ca/rates/price-indexes/cpi/"
    html = fetch_url(url)

    result = {}
    # HTML structure: <th>YYYY-MM</th><td>index</td><td>seasonal</td><td>YoY%</td>
    # The YoY% is in the column with header "th-percentage th-totalcpi2"
    pattern = re.compile(
        r'(\d{4}-\d{2})</th>'
        r"<td[^>]*>[\d.]+</td>"       # total CPI index
        r"<td[^>]*>[\d.]+</td>"       # seasonal adjusted
        r"<td[^>]*>([\-\d.]+)</td>"   # YoY percentage change
    )
    for m in pattern.finditer(html):
        period = m.group(1)
        value = float(m.group(2))
        if period >= "2016-01":
            result[period] = round1(value)

    if not result:
        print("  [警告] Bank of Canada CPIデータの取得に失敗。既存データを維持します。")
        return None

    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Central bank rate step functions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def build_rate_step_function(decisions, pre_rate):
    """Build monthly step-function series from central bank decision dates.
    decisions: list of (year, month, rate) tuples, sorted chronologically.
    pre_rate: rate before the first decision in the list.
    """
    current_rate = pre_rate
    decision_map = {}
    for y, m, r in decisions:
        key = f"{y:04d}-{m:02d}"
        decision_map[key] = r

    result = {}
    now = datetime.now()
    y, m = 2016, 1
    while (y, m) <= (now.year, now.month):
        key = f"{y:04d}-{m:02d}"
        if key in decision_map:
            current_rate = decision_map[key]
        result[key] = round(current_rate, 2)
        m += 1
        if m > 12:
            m = 1
            y += 1
    return result


def fetch_boe_bank_rate():
    """BoE Bank Rate decisions (hardcoded)."""
    decisions = [
        (2016, 8, 0.25),
        (2017, 11, 0.50),
        (2018, 8, 0.75),
        (2020, 3, 0.10),
        (2021, 12, 0.25),
        (2022, 2, 0.50),
        (2022, 3, 0.75),
        (2022, 5, 1.00),
        (2022, 6, 1.25),
        (2022, 8, 1.75),
        (2022, 9, 2.25),
        (2022, 11, 3.00),
        (2022, 12, 3.50),
        (2023, 2, 4.00),
        (2023, 3, 4.25),
        (2023, 5, 4.50),
        (2023, 6, 5.00),
        (2023, 8, 5.25),
        (2024, 8, 5.00),
        (2024, 11, 4.75),
        (2025, 2, 4.50),
        (2025, 5, 4.25),
    ]
    return build_rate_step_function(decisions, pre_rate=0.50)


def fetch_rba_cash_rate():
    """RBA Cash Rate Target decisions (hardcoded)."""
    decisions = [
        (2016, 5, 1.75),
        (2016, 8, 1.50),
        (2019, 6, 1.25),
        (2019, 7, 1.00),
        (2019, 10, 0.75),
        (2020, 3, 0.25),
        (2020, 11, 0.10),
        (2022, 5, 0.35),
        (2022, 6, 0.85),
        (2022, 7, 1.35),
        (2022, 8, 1.85),
        (2022, 9, 2.35),
        (2022, 10, 2.60),
        (2022, 11, 2.85),
        (2022, 12, 3.10),
        (2023, 2, 3.35),
        (2023, 3, 3.60),
        (2023, 5, 3.85),
        (2023, 6, 4.10),
        (2023, 11, 4.35),
        (2025, 2, 4.10),
        (2025, 5, 3.85),
        (2025, 8, 3.60),
        (2026, 2, 3.85),
        (2026, 3, 4.10),
    ]
    return build_rate_step_function(decisions, pre_rate=2.00)


def fetch_boj_policy_rate():
    """BoJ Policy Rate decisions (hardcoded)."""
    decisions = [
        (2016, 2, -0.10),
        (2024, 3, 0.00),
        (2024, 7, 0.25),
        (2025, 1, 0.50),
    ]
    return build_rate_step_function(decisions, pre_rate=-0.10)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  OECD SDMX API (CPI)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_oecd_cpi(country_code):
    """Fetch CPI YoY from OECD SDMX API.
    country_code: e.g. 'AUS', 'GBR'
    """
    url = (
        "https://sdmx.oecd.org/public/rest/data/"
        "OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,/"
        f"{country_code}.M.N.CPI.PA._T.N.GY"
        f"?startPeriod={START_DATE[:7]}"
    )
    text = fetch_url(url, accept="text/csv")
    result = {}
    for line in text.strip().split("\n")[1:]:
        cols = line.split(",")
        # TIME_PERIOD and OBS_VALUE positions
        period = cols[9]   # TIME_PERIOD
        value = cols[10]   # OBS_VALUE
        if period.startswith("20") and value:
            result[period] = round1(float(value))
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Build / merge country data
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def build_arrays(labels, *dicts):
    """Given a list of labels and dicts of {label: value}, return aligned arrays."""
    arrays = []
    for d in dicts:
        arrays.append([d.get(l) for l in labels])
    return arrays


def common_labels(*dicts):
    """Find the union of all labels, sorted."""
    all_labels = set()
    for d in dicts:
        if d:
            all_labels.update(d.keys())
    # Filter to 2016-01 onwards and sort
    labels = sorted(l for l in all_labels if l >= "2016-01")
    return labels


def trim_labels(labels, *arrays):
    """Trim trailing months where ALL indicators are None."""
    while labels:
        if all(arr[-1] is None for arr in arrays):
            labels.pop()
            for arr in arrays:
                arr.pop()
        else:
            break
    return labels, arrays


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Country updaters
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def update_us():
    print("\n🇺🇸 United States")
    print("  政策金利を取得中... (FRED: FEDFUNDS)")
    interest = fetch_fred("FEDFUNDS")
    print(f"    {len(interest)} ヶ月分取得")

    print("  CPIを取得中... (FRED: CPIAUCSL, YoY変換)")
    cpi = fetch_fred("CPIAUCSL", units="pc1")
    print(f"    {len(cpi)} ヶ月分取得")

    print("  失業率を取得中... (FRED: UNRATE)")
    unemployment = fetch_fred("UNRATE")
    print(f"    {len(unemployment)} ヶ月分取得")

    labels = common_labels(interest, cpi, unemployment)
    ir_arr, cpi_arr, unemp_arr = build_arrays(labels, interest, cpi, unemployment)
    labels, (ir_arr, cpi_arr, unemp_arr) = trim_labels(labels, ir_arr, cpi_arr, unemp_arr)

    data = {
        "country": "United States",
        "code": "us",
        "lastUpdated": datetime.now().strftime("%Y-%m-%d"),
        "sources": {
            "interestRate": "Federal Reserve (FRED FEDFUNDS)",
            "cpi": "Bureau of Labor Statistics (CPI-U 前年同月比)",
            "unemployment": "Bureau of Labor Statistics (LNS14000000)"
        },
        "labels": labels,
        "interestRate": ir_arr,
        "cpi": cpi_arr,
        "unemployment": unemp_arr
    }
    save_json("us", data)
    print(f"  期間: {labels[0]} ~ {labels[-1]}")


def update_ca():
    print("\n🇨🇦 Canada")
    print("  政策金利を取得中... (FRED: IRSTCI01CAM156N)")
    interest_raw = fetch_fred("IRSTCI01CAM156N")
    # Round to nearest 0.25 (BoC target rate increments)
    interest = {k: round(v * 4) / 4 if v is not None else None
                for k, v in interest_raw.items()}
    print(f"    {len(interest)} ヶ月分取得")

    print("  CPIを取得中... (Bank of Canada)")
    try:
        cpi = fetch_boc_cpi()
        if cpi:
            print(f"    {len(cpi)} ヶ月分取得")
        else:
            cpi = {}
    except Exception as e:
        print(f"    [エラー] {e}")
        cpi = {}

    # Fallback: if BoC scraping fails, try FRED (lagged)
    if not cpi:
        print("  CPI fallback: FRED (CPIAUCSL -> Canada版を検索中)...")
        existing = load_existing("ca")
        if existing:
            cpi = {l: v for l, v in zip(existing["labels"], existing["cpi"])}
            print(f"    既存データを維持 ({len(cpi)} ヶ月分)")

    print("  失業率を取得中... (FRED: LRUNTTTTCAM156S)")
    unemployment = fetch_fred("LRUNTTTTCAM156S")
    print(f"    {len(unemployment)} ヶ月分取得")

    labels = common_labels(interest, cpi, unemployment)
    ir_arr, cpi_arr, unemp_arr = build_arrays(labels, interest, cpi, unemployment)
    labels, (ir_arr, cpi_arr, unemp_arr) = trim_labels(labels, ir_arr, cpi_arr, unemp_arr)

    data = {
        "country": "Canada",
        "code": "ca",
        "lastUpdated": datetime.now().strftime("%Y-%m-%d"),
        "sources": {
            "interestRate": "Bank of Canada (Overnight Target Rate)",
            "cpi": "Bank of Canada / Statistics Canada (CPI 前年同月比)",
            "unemployment": "Statistics Canada (Labour Force Survey)"
        },
        "labels": labels,
        "interestRate": ir_arr,
        "cpi": cpi_arr,
        "unemployment": unemp_arr
    }
    save_json("ca", data)
    print(f"  期間: {labels[0]} ~ {labels[-1]}")


def update_eu():
    print("\n🇪🇺 Euro Area")
    print("  政策金利を取得中... (ECB Deposit Facility Rate)")
    try:
        interest = fetch_ecb_deposit_rate()
        if interest:
            print(f"    {len(interest)} ヶ月分取得")
        else:
            interest = {}
    except Exception as e:
        print(f"    [エラー] {e}")
        interest = {}

    # Fallback for interest rate
    if not interest:
        existing = load_existing("eu")
        if existing:
            interest = {l: v for l, v in zip(existing["labels"], existing["interestRate"])}
            print(f"    既存データを維持 ({len(interest)} ヶ月分)")

    print("  CPI (HICP) を取得中... (ECB Data API)")
    hicp = fetch_ecb_hicp()
    print(f"    {len(hicp)} ヶ月分取得")

    print("  失業率を取得中... (Eurostat API)")
    unemployment = fetch_eurostat_unemployment()
    print(f"    {len(unemployment)} ヶ月分取得")

    labels = common_labels(interest, hicp, unemployment)
    ir_arr, cpi_arr, unemp_arr = build_arrays(labels, interest, hicp, unemployment)
    labels, (ir_arr, cpi_arr, unemp_arr) = trim_labels(labels, ir_arr, cpi_arr, unemp_arr)

    data = {
        "country": "Euro Area",
        "code": "eu",
        "lastUpdated": datetime.now().strftime("%Y-%m-%d"),
        "sources": {
            "interestRate": "ECB (Deposit Facility Rate)",
            "cpi": "Eurostat (HICP 前年同月比)",
            "unemployment": "Eurostat (季節調整済)"
        },
        "labels": labels,
        "interestRate": ir_arr,
        "cpi": cpi_arr,
        "unemployment": unemp_arr
    }
    save_json("eu", data)
    print(f"  期間: {labels[0]} ~ {labels[-1]}")


def update_gb():
    print("\n🇬🇧 United Kingdom")
    print("  政策金利を生成中... (BoE Bank Rate)")
    interest = fetch_boe_bank_rate()
    print(f"    {len(interest)} ヶ月分生成")

    print("  CPIを取得中... (FRED: GBRCPIALLMINMEI, YoY変換)")
    cpi = fetch_fred("GBRCPIALLMINMEI", units="pc1")
    print(f"    {len(cpi)} ヶ月分取得")

    print("  失業率を取得中... (FRED: LRHUTTTTGBM156S)")
    unemployment = fetch_fred("LRHUTTTTGBM156S")
    print(f"    {len(unemployment)} ヶ月分取得")

    labels = common_labels(interest, cpi, unemployment)
    ir_arr, cpi_arr, unemp_arr = build_arrays(labels, interest, cpi, unemployment)
    labels, (ir_arr, cpi_arr, unemp_arr) = trim_labels(labels, ir_arr, cpi_arr, unemp_arr)

    data = {
        "country": "United Kingdom",
        "code": "gb",
        "lastUpdated": datetime.now().strftime("%Y-%m-%d"),
        "sources": {
            "interestRate": "Bank of England (Bank Rate)",
            "cpi": "ONS (CPI 前年同月比)",
            "unemployment": "ONS (Labour Force Survey)"
        },
        "labels": labels,
        "interestRate": ir_arr,
        "cpi": cpi_arr,
        "unemployment": unemp_arr
    }
    save_json("gb", data)
    print(f"  期間: {labels[0]} ~ {labels[-1]}")


def fetch_au_cpi():
    """Fetch Australia CPI: OECD monthly + FRED quarterly (forward-filled)."""
    # 1) Quarterly from FRED, forward-fill to monthly
    quarterly = fetch_fred("AUSCPIALLQINMEI", units="pc1")
    monthly = {}
    for label, val in sorted(quarterly.items()):
        if val is None:
            continue
        y, m = label.split("-")
        y, m = int(y), int(m)
        # Forward-fill quarter value to 3 months
        for offset in range(3):
            nm = m + offset
            ny = y
            if nm > 12:
                nm -= 12
                ny += 1
            key = f"{ny:04d}-{nm:02d}"
            monthly[key] = round1(val)

    # 2) Overlay with OECD monthly (more accurate, but limited period)
    try:
        oecd = fetch_oecd_cpi("AUS")
        monthly.update(oecd)
        print(f"    OECD月次: {len(oecd)} ヶ月分")
    except Exception as e:
        print(f"    OECD月次: [エラー] {e}")

    return monthly


def update_au():
    print("\n🇦🇺 Australia")
    print("  政策金利を生成中... (RBA Cash Rate Target)")
    interest = fetch_rba_cash_rate()
    print(f"    {len(interest)} ヶ月分生成")

    print("  CPIを取得中... (FRED四半期 + OECD月次)")
    try:
        cpi = fetch_au_cpi()
        print(f"    合計: {len(cpi)} ヶ月分")
    except Exception as e:
        print(f"    [エラー] {e}")
        cpi = {}
        existing = load_existing("au")
        if existing:
            cpi = {l: v for l, v in zip(existing["labels"], existing["cpi"])}
            print(f"    既存データを維持 ({len(cpi)} ヶ月分)")

    print("  失業率を取得中... (FRED: LRHUTTTTAUM156S)")
    unemployment = fetch_fred("LRHUTTTTAUM156S")
    print(f"    {len(unemployment)} ヶ月分取得")

    labels = common_labels(interest, cpi, unemployment)
    ir_arr, cpi_arr, unemp_arr = build_arrays(labels, interest, cpi, unemployment)
    labels, (ir_arr, cpi_arr, unemp_arr) = trim_labels(labels, ir_arr, cpi_arr, unemp_arr)

    data = {
        "country": "Australia",
        "code": "au",
        "lastUpdated": datetime.now().strftime("%Y-%m-%d"),
        "sources": {
            "interestRate": "RBA (Cash Rate Target)",
            "cpi": "OECD / ABS (CPI 前年同月比)",
            "unemployment": "ABS (Labour Force Survey)"
        },
        "labels": labels,
        "interestRate": ir_arr,
        "cpi": cpi_arr,
        "unemployment": unemp_arr
    }
    save_json("au", data)
    print(f"  期間: {labels[0]} ~ {labels[-1]}")


def update_jp():
    print("\n🇯🇵 Japan")
    print("  政策金利を生成中... (BoJ Policy Rate)")
    interest = fetch_boj_policy_rate()
    print(f"    {len(interest)} ヶ月分生成")

    print("  CPI: 現在利用可能な無料APIなし（FRED廃止済み）")
    cpi = {}

    print("  失業率を取得中... (FRED: LRHUTTTTJPM156S)")
    unemployment = fetch_fred("LRHUTTTTJPM156S")
    print(f"    {len(unemployment)} ヶ月分取得")

    labels = common_labels(interest, cpi, unemployment)
    ir_arr, cpi_arr, unemp_arr = build_arrays(labels, interest, cpi, unemployment)
    labels, (ir_arr, cpi_arr, unemp_arr) = trim_labels(labels, ir_arr, cpi_arr, unemp_arr)

    data = {
        "country": "Japan",
        "code": "jp",
        "lastUpdated": datetime.now().strftime("%Y-%m-%d"),
        "sources": {
            "interestRate": "日本銀行 (政策金利)",
            "cpi": "総務省統計局 (CPI 前年同月比) ※現在未取得",
            "unemployment": "総務省統計局 (労働力調査)"
        },
        "labels": labels,
        "interestRate": ir_arr,
        "cpi": cpi_arr,
        "unemployment": unemp_arr
    }
    save_json("jp", data)
    print(f"  期間: {labels[0]} ~ {labels[-1]}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Main
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    if not FRED_API_KEY:
        print("エラー: FRED_API_KEY 環境変数を設定してください")
        print("  export FRED_API_KEY=your_key_here")
        sys.exit(1)

    print("=" * 50)
    print("経済指標データ更新スクリプト")
    print(f"実行日時: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    # Select countries to update
    all_countries = ["us", "ca", "eu", "gb", "au", "jp"]
    targets = sys.argv[1:] if len(sys.argv) > 1 else all_countries

    updaters = {
        "us": update_us,
        "ca": update_ca,
        "eu": update_eu,
        "gb": update_gb,
        "au": update_au,
        "jp": update_jp,
    }

    for target in targets:
        try:
            if target in updaters:
                updaters[target]()
            else:
                print(f"\n[スキップ] 未対応の国コード: {target}")
        except Exception as e:
            print(f"\n[エラー] {target}: {e}")

    print("\n" + "=" * 50)
    print("完了！ git diff data/ で差分を確認してください")
    print("=" * 50)


if __name__ == "__main__":
    main()
