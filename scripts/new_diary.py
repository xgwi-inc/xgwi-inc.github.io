#!/usr/bin/env python3
"""
取引日誌入力スクリプト
対話式に日々の取引記録を data/diary.json に追記する。

データ構造:
  {
    "lastUpdated": "YYYY-MM-DDTHH:MM:SS+00:00",
    "count": N,
    "entries": [
      {
        "date": "YYYY-MM-DD",
        "trades": [
          { "symbol": "GBPUSD", "pnl_pips": -10.6 }
        ]
      }
    ]
  }

勝敗・合計 pips は trades から都度計算するため保存しない。
"""

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
DATA_PATH = SCRIPT_DIR.parent / "data" / "diary.json"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  純粋ロジック (テスト容易性のため I/O から分離)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def empty_diary():
    return {"lastUpdated": None, "count": 0, "entries": []}


def load_diary(path):
    if not Path(path).exists():
        return empty_diary()
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("entries", [])
    data.setdefault("count", len(data["entries"]))
    data.setdefault("lastUpdated", None)
    return data


def save_diary(path, data):
    data["count"] = len(data["entries"])
    data["lastUpdated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def summarize(trades):
    """trades から (wins, losses, total_pips) を返す。pnl_pips==0 はノーカウント。"""
    wins = sum(1 for t in trades if t["pnl_pips"] > 0)
    losses = sum(1 for t in trades if t["pnl_pips"] < 0)
    total = sum(t["pnl_pips"] for t in trades)
    # 浮動小数の誤差を抑えるため小数1桁に丸める
    return wins, losses, round(total, 1)


def format_pips(p):
    """+10.6pips / -10.6pips の表記を返す"""
    sign = "+" if p > 0 else ""
    return f"{sign}{p:g}pips"


def upsert_entry(data, entry, mode="merge"):
    """同じ日付があれば mode に従い merge / replace。新しい順に保つ。"""
    entries = data["entries"]
    for i, e in enumerate(entries):
        if e["date"] == entry["date"]:
            if mode == "replace":
                entries[i] = entry
            else:  # merge
                e["trades"].extend(entry["trades"])
            break
    else:
        entries.append(entry)
    entries.sort(key=lambda e: e["date"], reverse=True)
    return data


def recent_symbols(data, n=10):
    """直近の entries から重複を除いた銘柄リストを返す"""
    seen = []
    for e in data["entries"]:
        for t in e["trades"]:
            s = t["symbol"]
            if s not in seen:
                seen.append(s)
            if len(seen) >= n:
                return seen
    return seen


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  対話式 I/O
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _ask(prompt, default=None):
    suffix = f" [{default}]" if default is not None else ""
    s = input(f"{prompt}{suffix}: ").strip()
    return s if s else (default if default is not None else "")


def _ask_date():
    today = date.today().isoformat()
    while True:
        s = _ask("日付", today)
        try:
            return date.fromisoformat(s).isoformat()
        except ValueError:
            print("  YYYY-MM-DD 形式で入力してください")


def _ask_pips():
    while True:
        s = input("pips: ").strip()
        try:
            return float(s)
        except ValueError:
            print("  数値を入力してください (例: -10.6)")


def _ask_symbol(suggestions):
    if suggestions:
        print("  最近の銘柄: " + ", ".join(
            f"[{i+1}]{s}" for i, s in enumerate(suggestions)
        ))
    s = input("銘柄 (空 Enter で確定): ").strip()
    if not s:
        return None
    if s.isdigit():
        idx = int(s) - 1
        if 0 <= idx < len(suggestions):
            picked = suggestions[idx]
            print(f"  → {picked}")
            return picked
    return s


def collect_trades(suggestions):
    trades = []
    while True:
        symbol = _ask_symbol(suggestions)
        if symbol is None:
            break
        pnl = _ask_pips()
        trades.append({"symbol": symbol, "pnl_pips": pnl})
    return trades


def render_preview(entry):
    lines = [entry["date"]]
    if not entry["trades"]:
        lines.append("  ノートレ")
        return "\n".join(lines)
    for t in entry["trades"]:
        lines.append(f"  {t['symbol']}  {format_pips(t['pnl_pips'])}")
    wins, losses, total = summarize(entry["trades"])
    lines.append(f"  ─────────────")
    lines.append(f"  {wins}勝 {losses}敗  {format_pips(total)}")
    return "\n".join(lines)


def ask_yes_no(prompt, default=True):
    suffix = "[Y/n]" if default else "[y/N]"
    s = input(f"{prompt} {suffix}: ").strip().lower()
    if not s:
        return default
    return s in ("y", "yes")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  メイン
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    print("=" * 50)
    print("取引日誌")
    print("=" * 50)

    data = load_diary(DATA_PATH)

    entry_date = _ask_date()
    suggestions = recent_symbols(data)
    trades = collect_trades(suggestions)
    entry = {"date": entry_date, "trades": trades}

    mode = "merge"
    existing = next((e for e in data["entries"] if e["date"] == entry_date), None)
    if existing:
        print(f"\n{entry_date} は既に {len(existing['trades'])} 件の記録があります。")
        if not ask_yes_no("既存に追記しますか? (n で全置換)", default=True):
            mode = "replace"

    print("\n" + render_preview(entry))
    print()
    if not ask_yes_no("書き込みますか?", default=True):
        print("中止しました")
        return 1

    upsert_entry(data, entry, mode=mode)
    save_diary(DATA_PATH, data)
    print(f"✓ {DATA_PATH} に保存しました ({data['count']} 日分)")
    print()
    print("コミット用コマンド:")
    print(f'  git add {DATA_PATH.relative_to(SCRIPT_DIR.parent)}')
    print(f'  git commit -m "diary: {entry_date}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
