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
  AU     - ABS Data API (CPI/unemployment) + RBA Cash Rate via DBnomics (BIS fallback)
  JP     - e-Stat Dashboard API (CPI) + FRED API (unemployment) + BoJ Policy Rate (hardcoded)
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

def fetch_url(url, accept=None, retries=3, backoff=2):
    """Fetch a URL as text with retry-on-transient-error and gzip handling."""
    import time
    headers = {
        "User-Agent": "Mozilla/5.0 (economic-data-updater)",
        "Accept-Encoding": "identity",  # suppress gzip (Python urllib doesn't auto-decompress)
    }
    if accept:
        headers["Accept"] = accept
    last_err = None
    for attempt in range(retries):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=60) as resp:
                raw = resp.read()
            if raw[:2] == b"\x1f\x8b":
                import gzip
                raw = gzip.decompress(raw)
            return raw.decode("utf-8")
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
    raise last_err


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
#  ECB Data API — policy rate step series (FM dataflow)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_ecb_deposit_rate():
    """ECB Deposit Facility Rate. FM dataflow emits one row per rate change.
    Build month-end step series from 2016-01 onwards.
    """
    url = (
        "https://data-api.ecb.europa.eu/service/data/FM/"
        "B.U2.EUR.4F.KR.DFR.LEV?format=csvdata"
    )
    text = fetch_url(url, accept="text/csv")
    # Each row: ...,TIME_PERIOD,OBS_VALUE,...
    # Parse header to find column indices
    lines = text.strip().split("\n")
    header = lines[0].split(",")
    try:
        tp_idx = header.index("TIME_PERIOD")
        ov_idx = header.index("OBS_VALUE")
    except ValueError:
        print("  [警告] ECB FM CSVヘッダー解析失敗")
        return None

    decisions = []
    for line in lines[1:]:
        cols = line.split(",")
        if len(cols) <= max(tp_idx, ov_idx):
            continue
        date = cols[tp_idx]
        val = cols[ov_idx]
        if not date or not val:
            continue
        try:
            rate = float(val)
        except ValueError:
            continue
        decisions.append((date, rate))

    if not decisions:
        return None

    decisions.sort()
    # Forward-fill to month-end starting from 2016-01
    current_rate = decisions[0][1]
    for date, rate in decisions:
        if date[:7] < "2016-01":
            current_rate = rate

    decision_map = {}
    for date, rate in decisions:
        decision_map[date[:7]] = rate

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
            m, y = 1, y + 1
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Eurostat JSON-stat 2.0 helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_eurostat_jsonstat(dataflow, params):
    """Fetch an Eurostat JSON-stat 2.0 dataset and return {YYYY-MM: value}.
    `params` is a dict of query params (e.g. geo=EA20, unit=...). The time
    dimension is auto-detected.
    """
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{dataflow}?{qs}"
    data = fetch_json(url)
    # JSON-stat 2.0: find the time dimension
    dims = data.get("dimension", {})
    time_dim_name = None
    for name, meta in dims.items():
        role = data.get("role", {}).get("time", [])
        if name in role or meta.get("label", "").lower() in ("time", "period"):
            time_dim_name = name
            break
    if time_dim_name is None:
        # fallback: use the last dimension listed in 'id'
        ids = data.get("id", [])
        time_dim_name = ids[-1] if ids else None
    if not time_dim_name:
        return {}
    time_cat = dims[time_dim_name]["category"]["index"]
    # index map: {period: idx}
    if isinstance(time_cat, dict):
        idx_map = time_cat
    else:
        idx_map = {p: i for i, p in enumerate(time_cat)}

    # Determine size of non-time dimensions so we can compute flat index.
    # For single-point queries (all other dims fixed), size of others = 1.
    # JSON-stat flat index = sum(idx_i * stride_i)
    ids = data.get("id", [])
    size = data.get("size", [])
    strides = [1] * len(size)
    for i in range(len(size) - 2, -1, -1):
        strides[i] = strides[i + 1] * size[i + 1]
    time_pos = ids.index(time_dim_name)

    values = data.get("value", {})
    # values may be dict {flat_idx_str: number} or list
    def get_val(flat_idx):
        if isinstance(values, dict):
            return values.get(str(flat_idx))
        if 0 <= flat_idx < len(values):
            return values[flat_idx]
        return None

    # Non-time dims are all singletons for our queries
    result = {}
    for period, t_idx in idx_map.items():
        flat = t_idx * strides[time_pos]
        v = get_val(flat)
        if v is not None:
            result[period] = float(v)
    return result


def fetch_eurostat_hicp_yoy():
    """Euro Area HICP YoY%. Merge long-history table with fresh short-term table
    (teicp000 is already rebased to 2025=100 and carries post-2025-12 data).
    """
    # Historical: prc_hicp_manr (YoY, pre-rebase, ends ~2025-12)
    historical = fetch_eurostat_jsonstat(
        "prc_hicp_manr",
        {"coicop": "CP00", "geo": "EA20", "unit": "RCH_A", "sinceTimePeriod": "2016-01"},
    )
    # Fresh tail: teicp000 (YoY, post-rebase)
    try:
        fresh = fetch_eurostat_jsonstat(
            "teicp000",
            {"geo": "EA20", "unit": "PCH_M12"},
        )
    except Exception as e:
        print(f"    teicp000 fetch failed: {e}")
        fresh = {}

    merged = {k: round1(v) for k, v in historical.items() if v is not None}
    for k, v in fresh.items():
        if v is None:
            continue
        merged[k] = round1(v)  # fresh wins in overlap
    return merged


def fetch_eurostat_unemployment_ea20():
    """Euro Area unemployment rate (seasonally adjusted, total).
    Uses EA21 (Bulgaria joined 2025-01) — Eurostat retired EA20 in une_rt_m.
    """
    return {
        k: round1(v)
        for k, v in fetch_eurostat_jsonstat(
            "une_rt_m",
            {
                "geo": "EA21",
                "age": "TOTAL",
                "sex": "T",
                "unit": "PC_ACT",
                "s_adj": "SA",
                "sinceTimePeriod": "2016-01",
            },
        ).items()
        if v is not None
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Bank of Canada Valet API
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_boc_valet(series_id, start=START_DATE):
    """Fetch a BoC Valet series (daily) and collapse to monthly (last value per month)."""
    url = (
        f"https://www.bankofcanada.ca/valet/observations/{series_id}/json"
        f"?start_date={start}"
    )
    data = fetch_json(url)
    monthly = {}
    for obs in data.get("observations", []):
        date = obs.get("d", "")
        if len(date) < 7:
            continue
        val = obs.get(series_id, {}).get("v")
        if val in (None, ""):
            continue
        monthly[date[:7]] = float(val)  # later dates overwrite = month-end
    return monthly


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  StatsCan WDS REST API
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_statcan_vector(vector_id, start=START_DATE):
    """Fetch a StatsCan time series by vector ID (monthly).
    Returns {YYYY-MM: float_value} (raw values, no transform).
    """
    end = datetime.now().strftime("%Y-%m-01")
    url = (
        "https://www150.statcan.gc.ca/t1/wds/rest/getDataFromVectorByReferencePeriodRange"
        f"?vectorIds={vector_id}"
        f"&startRefPeriod={start[:7]}-01"
        f"&endReferencePeriod={end}"
    )
    data = fetch_json(url)
    points = data[0]["object"]["vectorDataPoint"]
    result = {}
    for p in points:
        ref = p.get("refPer", "")[:7]
        val = p.get("value")
        if ref and val is not None:
            result[ref] = float(val)
    return result


def fetch_statcan_cpi_yoy():
    """CPI All-items (vector 41690973) as YoY%. Fetch 13+ extra months for lookback."""
    # Fetch 12 months earlier to enable YoY computation for the first target period.
    y = int(START_DATE[:4]) - 1
    m = int(START_DATE[5:7])
    extended_start = f"{y:04d}-{m:02d}-01"
    index = fetch_statcan_vector(41690973, start=extended_start)
    result = {}
    for period, val in index.items():
        y, mo = period.split("-")
        prev = f"{int(y)-1}-{mo}"
        if prev in index and index[prev] not in (None, 0):
            yoy = (val / index[prev] - 1) * 100
            if period >= START_DATE[:7]:
                result[period] = round1(yoy)
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Central bank rate step functions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  e-Stat Dashboard API (single-call for full history)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_estat_series(indicator_code, seasonal_code=None, start="2016-01"):
    """Fetch a Japan e-Stat Dashboard indicator for the whole time range in one call.
    seasonal_code: "1"=原数値, "2"=季節調整値. None = accept all.
    Returns {YYYY-MM: float_value}.
    """
    url = (
        "https://dashboard.e-stat.go.jp/api/1.0/Json/getData"
        f"?Lang=JP&IndicatorCode={indicator_code}"
        "&RegionCode=00000&Cycle=1"
    )
    data = fetch_json(url)
    objs = (
        data.get("GET_STATS", {})
            .get("STATISTICAL_DATA", {})
            .get("DATA_INF", {})
            .get("DATA_OBJ", [])
    )
    if isinstance(objs, dict):
        objs = [objs]
    result = {}
    for obj in objs:
        values = obj.get("VALUE", [])
        if isinstance(values, dict):
            values = [values]
        for v in values:
            if seasonal_code and v.get("@isSeasonal") != seasonal_code:
                continue
            time_code = v.get("@time", "")
            # "YYYYMM00" -> "YYYY-MM"
            if len(time_code) < 6 or not time_code[:6].isdigit():
                continue
            key = f"{time_code[:4]}-{time_code[4:6]}"
            if key < start:
                continue
            try:
                result[key] = round1(float(v.get("$", "")))
            except (TypeError, ValueError):
                continue
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  ABS Data API (Australia)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_abs_sdmx_csv(dataflow, key, start=START_DATE):
    """Fetch an ABS time series via SDMX-CSV. Returns {YYYY-MM: value}."""
    url = (
        f"https://data.api.abs.gov.au/data/{dataflow}/{key}"
        f"?startPeriod={start[:7]}"
    )
    text = fetch_url(url, accept="application/vnd.sdmx.data+csv")
    lines = text.strip().split("\n")
    if len(lines) < 2:
        return {}
    header = lines[0].split(",")
    try:
        tp_idx = header.index("TIME_PERIOD")
        ov_idx = header.index("OBS_VALUE")
    except ValueError:
        return {}
    result = {}
    for line in lines[1:]:
        cols = line.split(",")
        if len(cols) <= max(tp_idx, ov_idx):
            continue
        period = cols[tp_idx]
        val = cols[ov_idx]
        if not period or not val:
            continue
        try:
            result[period] = float(val)
        except ValueError:
            continue
    return result


def fetch_abs_cpi_yoy():
    """Australia Monthly CPI Indicator (YoY%) — splice two dataflows.

    `CPI_M` covers 2018-09 ~ 2025-09 (series frozen in 2025-09 after re-weighting);
    `CPI` covers the re-weighted series from 2025-04 onwards. Merge with `CPI`
    taking precedence for overlap (it is the current authoritative series).
    """
    merged = {}
    try:
        old = fetch_abs_sdmx_csv("CPI_M", "3.999904.10.50.M")
        for k, v in old.items():
            merged[k] = round1(v)
    except Exception as e:
        print(f"    ABS CPI_M: [エラー] {e}")
    try:
        new = fetch_abs_sdmx_csv("CPI", "3.999904.10.50.M")
        for k, v in new.items():
            merged[k] = round1(v)
    except Exception as e:
        print(f"    ABS CPI: [エラー] {e}")
    return merged


def fetch_abs_unemployment():
    """Australia unemployment rate (15+, SA).
    Dataflow LF: MEASURE=M14, SEX=3 (persons), AGE=1599, TSEST=20 (SA), REGION=AUS, FREQ=M.
    """
    raw = fetch_abs_sdmx_csv("LF", "M14.3.1599.20.AUS.M")
    return {k: round1(v) for k, v in raw.items()}


def fetch_rba_cash_rate():
    """RBA Cash Rate Target via DBnomics (RBA A2 / ARBAMPCNCRT — "New Cash Rate Target").

    Event-based series (one row per decision). Forward-fill to month-end starting
    from 2016-01, matching the convention used elsewhere. DBnomics typically lists
    a decision within hours of the RBA announcement, so May 2026's 4.35 lands in
    the same week — much faster than BIS WS_CBPOL, which lags 1–2 weeks.
    """
    url = "https://api.db.nomics.world/v22/series/RBA/A2/ARBAMPCNCRT?observations=1"
    data = fetch_json(url)
    docs = data.get("series", {}).get("docs", [])
    if not docs:
        return None
    s = docs[0]
    decisions = []
    for p, v in zip(s.get("period", []), s.get("value", [])):
        if v in (None, "NA", "", "."):
            continue
        try:
            decisions.append((p, float(v)))
        except (TypeError, ValueError):
            continue
    if not decisions:
        return None
    decisions.sort()

    decision_map = {}
    current_rate = decisions[0][1]
    for date, rate in decisions:
        if date[:7] < "2016-01":
            current_rate = rate
            continue
        decision_map[date[:7]] = rate  # last decision in a given month wins

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
            m, y = 1, y + 1
    return result


def fetch_bis_policy_rate(country_code):
    """Fetch central bank policy rate from BIS SDMX API (no key needed).
    Daily data -> extract last valid value per month.
    country_code: ISO 2-letter code (GB, AU, JP)
    """
    url = (
        f"https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/"
        f"D.{country_code}?startPeriod={START_DATE[:7]}"
    )
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (economic-data-updater)"})
    with urlopen(req, timeout=60) as resp:
        xml_text = resp.read().decode("utf-8")

    # Parse daily observations -> monthly (last valid value per month)
    monthly = {}
    for m in re.finditer(
        r'TIME_PERIOD="(\d{4}-\d{2})-\d{2}"\s+OBS_VALUE="(-?[\d.]+)"',
        xml_text,
    ):
        period = m.group(1)
        value = round(float(m.group(2)), 2)
        monthly[period] = value  # later dates overwrite = month-end value

    # Back-fill gap at the start: if data starts after START_DATE,
    # fill earlier months with the earliest known rate
    if monthly:
        earliest_key = min(monthly)
        earliest_val = monthly[earliest_key]
        y, m = int(START_DATE[:4]), int(START_DATE[5:7])
        ey, em = int(earliest_key[:4]), int(earliest_key[5:7])
        while (y, m) < (ey, em):
            key = f"{y:04d}-{m:02d}"
            monthly[key] = earliest_val
            m += 1
            if m > 12:
                m = 1
                y += 1

    return monthly


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
    print("  政策金利を取得中... (BoC Valet: V39079)")
    try:
        interest = fetch_boc_valet("V39079")
        print(f"    {len(interest)} ヶ月分取得")
    except Exception as e:
        print(f"    [エラー] {e}, FREDにフォールバック")
        interest_raw = fetch_fred("IRSTCI01CAM156N")
        interest = {k: round(v * 4) / 4 if v is not None else None
                    for k, v in interest_raw.items()}

    print("  CPIを取得中... (StatsCan vector 41690973, YoY算出)")
    try:
        cpi = fetch_statcan_cpi_yoy()
        print(f"    {len(cpi)} ヶ月分取得")
    except Exception as e:
        print(f"    [エラー] {e}")
        cpi = {}
        existing = load_existing("ca")
        if existing:
            cpi = {l: v for l, v in zip(existing["labels"], existing["cpi"])}
            print(f"    既存データを維持 ({len(cpi)} ヶ月分)")

    print("  失業率を取得中... (StatsCan vector 2062815)")
    try:
        unemp_raw = fetch_statcan_vector(2062815)
        unemployment = {k: round1(v) for k, v in unemp_raw.items()}
        print(f"    {len(unemployment)} ヶ月分取得")
    except Exception as e:
        print(f"    [エラー] {e}, FREDにフォールバック")
        unemployment = fetch_fred("LRUNTTTTCAM156S")

    labels = common_labels(interest, cpi, unemployment)
    ir_arr, cpi_arr, unemp_arr = build_arrays(labels, interest, cpi, unemployment)
    labels, (ir_arr, cpi_arr, unemp_arr) = trim_labels(labels, ir_arr, cpi_arr, unemp_arr)

    data = {
        "country": "Canada",
        "code": "ca",
        "lastUpdated": datetime.now().strftime("%Y-%m-%d"),
        "sources": {
            "interestRate": "Bank of Canada (Valet V39079, Overnight Target Rate)",
            "cpi": "Statistics Canada (Vector 41690973, CPI All-items YoY)",
            "unemployment": "Statistics Canada (Vector 2062815, LFS 15+ SA)"
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
    print("  政策金利を取得中... (ECB Data API: FM DFR)")
    try:
        interest = fetch_ecb_deposit_rate()
        if not interest:
            raise ValueError("empty result")
        print(f"    {len(interest)} ヶ月分取得")
    except Exception as e:
        print(f"    [エラー] {e}")
        interest = {}
        existing = load_existing("eu")
        if existing:
            interest = {l: v for l, v in zip(existing["labels"], existing["interestRate"])}
            print(f"    既存データを維持 ({len(interest)} ヶ月分)")

    print("  CPI (HICP) を取得中... (Eurostat prc_hicp_manr + teicp000 合成)")
    try:
        hicp = fetch_eurostat_hicp_yoy()
        print(f"    {len(hicp)} ヶ月分取得")
    except Exception as e:
        print(f"    [エラー] {e}")
        hicp = {}
        existing = load_existing("eu")
        if existing:
            hicp = {l: v for l, v in zip(existing["labels"], existing["cpi"])}

    print("  失業率を取得中... (Eurostat une_rt_m)")
    try:
        unemployment = fetch_eurostat_unemployment_ea20()
        print(f"    {len(unemployment)} ヶ月分取得")
    except Exception as e:
        print(f"    [エラー] {e}")
        unemployment = {}

    labels = common_labels(interest, hicp, unemployment)
    ir_arr, cpi_arr, unemp_arr = build_arrays(labels, interest, hicp, unemployment)
    labels, (ir_arr, cpi_arr, unemp_arr) = trim_labels(labels, ir_arr, cpi_arr, unemp_arr)

    data = {
        "country": "Euro Area",
        "code": "eu",
        "lastUpdated": datetime.now().strftime("%Y-%m-%d"),
        "sources": {
            "interestRate": "ECB Data API (FM DFR, Deposit Facility Rate)",
            "cpi": "Eurostat (HICP YoY, prc_hicp_manr + teicp000 合成)",
            "unemployment": "Eurostat (une_rt_m, EA21 SA)"
        },
        "labels": labels,
        "interestRate": ir_arr,
        "cpi": cpi_arr,
        "unemployment": unemp_arr
    }
    save_json("eu", data)
    print(f"  期間: {labels[0]} ~ {labels[-1]}")


MONTH_ABBR_TO_NUM = {
    'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
    'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12,
}


def fetch_ons_unemployment():
    """Fetch UK unemployment rate (aged 16+, SA) from ONS timeseries MGSX.
    Value is a 3-month rolling average. ONS labels by the middle month in
    the `date` field but the `label` field (e.g. "2025 DEC-FEB") carries the
    actual 3-month window. Market convention references the end month, so we
    key each observation by the end month of the window.
    """
    url = "https://www.ons.gov.uk/employmentandlabourmarket/peoplenotinwork/unemployment/timeseries/mgsx/lms/data"
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (economic-data-updater)", "Accept": "application/json"})
    with urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    result = {}
    for m in data.get("months", []):
        label = m.get("label", "")
        # Expected format: "YYYY START-END" e.g. "2025 DEC-FEB"
        parts = label.split()
        if len(parts) != 2 or '-' not in parts[1]:
            continue
        year_str, window = parts
        start_abbr, _, end_abbr = window.partition('-')
        if (not year_str.isdigit()
                or start_abbr not in MONTH_ABBR_TO_NUM
                or end_abbr not in MONTH_ABBR_TO_NUM):
            continue
        start_year = int(year_str)
        if start_year < 2016:
            continue
        start_month = MONTH_ABBR_TO_NUM[start_abbr]
        end_month = MONTH_ABBR_TO_NUM[end_abbr]
        end_year = start_year + 1 if end_month < start_month else start_year
        key = f"{end_year:04d}-{end_month:02d}"
        try:
            result[key] = round1(float(m["value"]))
        except (ValueError, KeyError):
            continue
    return result


def update_gb():
    print("\n🇬🇧 United Kingdom")
    print("  政策金利を取得中... (BIS: BoE Bank Rate)")
    try:
        interest = fetch_bis_policy_rate("GB")
        print(f"    {len(interest)} ヶ月分取得")
    except Exception as e:
        print(f"    [エラー] {e}")
        interest = {}
        existing = load_existing("gb")
        if existing:
            interest = {l: v for l, v in zip(existing["labels"], existing["interestRate"])}
            print(f"    既存データを維持 ({len(interest)} ヶ月分)")

    print("  CPIを取得中... (OECD: GBR CPI YoY)")
    try:
        cpi = fetch_oecd_cpi("GBR")
        print(f"    {len(cpi)} ヶ月分取得")
    except Exception as e:
        print(f"    OECD [エラー] {e}, FREDにフォールバック")
        cpi = fetch_fred("GBRCPIALLMINMEI", units="pc1")
        print(f"    {len(cpi)} ヶ月分取得 (FRED)")

    print("  失業率を取得中... (ONS: MGSX)")
    try:
        unemployment = fetch_ons_unemployment()
        print(f"    {len(unemployment)} ヶ月分取得")
    except Exception as e:
        print(f"    ONS [エラー] {e}, FREDにフォールバック")
        unemployment = fetch_fred("LRHUTTTTGBM156S")
        print(f"    {len(unemployment)} ヶ月分取得 (FRED)")

    labels = common_labels(interest, cpi, unemployment)
    ir_arr, cpi_arr, unemp_arr = build_arrays(labels, interest, cpi, unemployment)
    labels, (ir_arr, cpi_arr, unemp_arr) = trim_labels(labels, ir_arr, cpi_arr, unemp_arr)

    data = {
        "country": "United Kingdom",
        "code": "gb",
        "lastUpdated": datetime.now().strftime("%Y-%m-%d"),
        "sources": {
            "interestRate": "Bank of England (Bank Rate)",
            "cpi": "OECD (CPI 前年同月比)",
            "unemployment": "ONS (Labour Force Survey, MGSX)"
        },
        "labels": labels,
        "interestRate": ir_arr,
        "cpi": cpi_arr,
        "unemployment": unemp_arr
    }
    save_json("gb", data)
    print(f"  期間: {labels[0]} ~ {labels[-1]}")


def fetch_au_cpi():
    """Fetch Australia CPI: ABS monthly indicator (2018-09+) with FRED quarterly backfill."""
    # 1) Quarterly from FRED, forward-fill to monthly (covers pre-2018-09)
    monthly = {}
    try:
        quarterly = fetch_fred("AUSCPIALLQINMEI", units="pc1")
        for label, val in sorted(quarterly.items()):
            if val is None:
                continue
            y, m = label.split("-")
            y, m = int(y), int(m)
            for offset in range(3):
                nm = m + offset
                ny = y
                if nm > 12:
                    nm -= 12
                    ny += 1
                key = f"{ny:04d}-{nm:02d}"
                monthly[key] = round1(val)
        print(f"    FRED四半期フィル: {len(monthly)} ヶ月分")
    except Exception as e:
        print(f"    FRED四半期: [エラー] {e}")

    # 2) Overlay with ABS monthly indicator (authoritative, fresh)
    try:
        abs_monthly = fetch_abs_cpi_yoy()
        monthly.update(abs_monthly)
        print(f"    ABS月次上書き: {len(abs_monthly)} ヶ月分")
    except Exception as e:
        print(f"    ABS月次: [エラー] {e}")

    return monthly


def update_au():
    print("\n🇦🇺 Australia")
    print("  政策金利を取得中... (DBnomics RBA/A2/ARBAMPCNCRT, BIS fallback)")
    interest = None
    try:
        interest = fetch_rba_cash_rate()
        if interest:
            print(f"    {len(interest)} ヶ月分取得 (RBA A2)")
    except Exception as e:
        print(f"    RBA A2 [エラー] {e}, BISにフォールバック")
    if not interest:
        try:
            interest = fetch_bis_policy_rate("AU")
            print(f"    {len(interest)} ヶ月分取得 (BIS)")
        except Exception as e:
            print(f"    BIS [エラー] {e}")
            interest = {}
            existing = load_existing("au")
            if existing:
                interest = {l: v for l, v in zip(existing["labels"], existing["interestRate"])}
                print(f"    既存データを維持 ({len(interest)} ヶ月分)")

    print("  CPIを取得中... (ABS月次 + FRED四半期フォールバック)")
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

    print("  失業率を取得中... (ABS LF: M14.3.1599.20.AUS.M)")
    try:
        unemployment = fetch_abs_unemployment()
        print(f"    {len(unemployment)} ヶ月分取得")
    except Exception as e:
        print(f"    [エラー] {e}, FREDにフォールバック")
        unemployment = fetch_fred("LRHUTTTTAUM156S")

    labels = common_labels(interest, cpi, unemployment)
    ir_arr, cpi_arr, unemp_arr = build_arrays(labels, interest, cpi, unemployment)
    labels, (ir_arr, cpi_arr, unemp_arr) = trim_labels(labels, ir_arr, cpi_arr, unemp_arr)

    data = {
        "country": "Australia",
        "code": "au",
        "lastUpdated": datetime.now().strftime("%Y-%m-%d"),
        "sources": {
            "interestRate": "RBA A2 (Cash Rate Target via DBnomics, BIS fallback)",
            "cpi": "ABS Monthly CPI Indicator + FRED quarterly backfill",
            "unemployment": "ABS Labour Force Survey (15+ SA)"
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
    print("  政策金利を取得中... (BIS: BoJ Policy Rate)")
    try:
        interest = fetch_bis_policy_rate("JP")
        print(f"    {len(interest)} ヶ月分取得")
    except Exception as e:
        print(f"    [エラー] {e}")
        interest = {}
        existing = load_existing("jp")
        if existing:
            interest = {l: v for l, v in zip(existing["labels"], existing["interestRate"])}
            print(f"    既存データを維持 ({len(interest)} ヶ月分)")

    print("  CPIを取得中... (e-Stat IndicatorCode 0703010501010030000 単一呼び出し)")
    try:
        cpi = fetch_estat_series("0703010501010030000", seasonal_code=None)
        print(f"    {len(cpi)} ヶ月分取得")
    except Exception as e:
        print(f"    [エラー] {e}")
        cpi = {}
        existing = load_existing("jp")
        if existing:
            cpi = {l: v for l, v in zip(existing["labels"], existing["cpi"])}
            print(f"    既存データを維持 ({len(cpi)} ヶ月分)")

    print("  失業率を取得中... (e-Stat IndicatorCode 0301010000020020010, 季節調整値)")
    try:
        unemployment = fetch_estat_series("0301010000020020010", seasonal_code="2")
        print(f"    {len(unemployment)} ヶ月分取得")
    except Exception as e:
        print(f"    [エラー] {e}, FREDにフォールバック")
        unemployment = fetch_fred("LRHUTTTTJPM156S")

    labels = common_labels(interest, cpi, unemployment)
    ir_arr, cpi_arr, unemp_arr = build_arrays(labels, interest, cpi, unemployment)
    labels, (ir_arr, cpi_arr, unemp_arr) = trim_labels(labels, ir_arr, cpi_arr, unemp_arr)

    data = {
        "country": "Japan",
        "code": "jp",
        "lastUpdated": datetime.now().strftime("%Y-%m-%d"),
        "sources": {
            "interestRate": "BIS WS_CBPOL D.JP (BoJ Policy Rate)",
            "cpi": "総務省統計局 (e-Stat Dashboard, CPI 前年同月比 2020年基準)",
            "unemployment": "総務省統計局 (e-Stat Dashboard, 労働力調査 完全失業率 季節調整値)"
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
