#!/usr/bin/env python3
"""
中央銀行関係者の発言に関するReuters日本語記事を取得するスクリプト

使い方:
  python scripts/fetch_cb_articles.py

データソース:
  Google News RSS → jp.reuters.com 記事をフィルタリング
"""

import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen, Request
from xml.etree import ElementTree

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR.parent / "data"
OUTPUT_PATH = DATA_DIR / 'cb_articles.json'

# Google News RSS search queries (jp.reuters.com の中央銀行・IMF 金利関連記事)
QUERIES = [
    'site:jp.reuters.com (日銀 OR 植田) (利上げ OR 利下げ OR 金利 OR 政策金利)',
    'site:jp.reuters.com (FRB OR パウエル OR 連邦準備) (利上げ OR 利下げ OR 金利 OR 政策金利)',
    'site:jp.reuters.com (連銀 OR 地区連銀) (利上げ OR 利下げ OR 金利 OR 総裁)',
    'site:jp.reuters.com (ECB OR ラガルド OR 欧州中銀) (利上げ OR 利下げ OR 金利 OR 政策金利)',
    'site:jp.reuters.com (英中銀 OR BOE OR ベイリー) (利上げ OR 利下げ OR 金利)',
    'site:jp.reuters.com (RBA OR 豪中銀 OR 豪準備銀行) (利上げ OR 利下げ OR 金利)',
    'site:jp.reuters.com (カナダ中銀 OR カナダ銀行 OR マックレム) (利上げ OR 利下げ OR 金利)',
    'site:jp.reuters.com (IMF OR 国際通貨基金) (利上げ OR 利下げ OR 金利 OR インフレ)',
    'site:jp.reuters.com (政策金利 OR 金融政策) (据え置き OR 引き上げ OR 引き下げ)',
]

# タイトルに含まれるべきキーワード（対象: 日銀・FRB・連銀・ECB・BOE・RBA・BOC・IMF）
CB_KEYWORDS = [
    # 日銀
    r'日銀', r'植田',
    # FRB・連銀
    r'FRB', r'パウエル', r'連邦準備', r'連銀',
    r'ウォラー', r'ボウマン', r'ジェファーソン', r'クーグラー',
    r'ウィリアムズ', r'デーリー', r'グールズビー', r'ハマック',
    r'ムサレム', r'バーキン', r'ボスティック', r'ハーカー',
    r'カシュカリ', r'シュミッド', r'コリンズ', r'ローガン',
    # ECB
    r'ECB', r'ラガルド', r'欧州中銀', r'欧州中央銀行',
    r'シュナーベル', r'レーン', r'ナーゲル', r'ビルロワ',
    r'パネッタ', r'デギンドス', r'ホルツマン', r'クノット',
    r'レーン', r'センテノ',
    # BOE
    r'英中銀', r'BOE', r'ベイリー', r'イングランド銀行',
    # RBA
    r'RBA', r'豪中銀', r'豪準備銀行', r'ブロック',
    # BOC
    r'カナダ中銀', r'カナダ銀行', r'マックレム',
    # IMF
    r'IMF', r'国際通貨基金',
]

# タイトルに含まれるべきキーワード（金利関連）
RATE_KEYWORDS = [
    r'利上げ', r'利下げ', r'金利', r'政策金利',
    r'据え置き', r'引き上げ', r'引き下げ',
    r'利回り',
]


def fetch_rss(query):
    """Google News RSSを取得（日本語対応）"""
    encoded = quote(query, safe='():+')
    url = f'https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP:ja'
    req = Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    with urlopen(req, timeout=15) as resp:
        return resp.read().decode('utf-8')


def is_cb_rate_article(title):
    """タイトルが中央銀行/IMFの金利関連かどうか判定"""
    # Reutersは ＥＣＢ/ＦＲＢ 等を全角で書くので正規化してから照合
    normalized = unicodedata.normalize('NFKC', title)
    has_cb = any(re.search(kw, normalized) for kw in CB_KEYWORDS)
    has_rate = any(re.search(kw, normalized) for kw in RATE_KEYWORDS)
    return has_cb and has_rate


def load_existing_articles():
    """既存のJSONを読み込み、title -> article の辞書で返す"""
    if not OUTPUT_PATH.exists():
        return {}
    try:
        with open(OUTPUT_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return {a['title']: a for a in data.get('articles', [])}
    except (OSError, json.JSONDecodeError) as e:
        print(f"[警告] 既存データの読み込みに失敗: {e}")
        return {}


def main():
    articles = load_existing_articles()
    existing_count = len(articles)

    print("=" * 50)
    print("中央銀行関係者 Reuters記事取得スクリプト (日本語版)")
    print(f"実行日時: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"既存記事: {existing_count} 件 (積み重ねモード)")
    print("=" * 50)

    for i, query in enumerate(QUERIES):
        print(f"\n[{i+1}/{len(QUERIES)}] {query[:70]}...")
        try:
            xml_text = fetch_rss(query)
            root = ElementTree.fromstring(xml_text)
            items = root.findall('.//item')
            count = 0

            for item in items:
                title = item.find('title').text or ''
                # 末尾の " - Reuters" を除去
                if title.endswith(' - Reuters'):
                    title = title[:-len(' - Reuters')]

                link = item.find('link').text or ''
                pub_date_str = item.find('pubDate').text or ''
                source = item.find('source')
                source_text = source.text if source is not None else ''

                # Reuters記事のみ
                if source_text != 'Reuters':
                    continue

                try:
                    pub_date = parsedate_to_datetime(pub_date_str)
                except Exception:
                    continue

                # 中央銀行関連のフィルタ
                if not is_cb_rate_article(title):
                    continue

                if title not in articles:
                    articles[title] = {
                        'title': title,
                        'link': link,
                        'pubDate': pub_date.isoformat(),
                    }
                    count += 1

            print(f"  -> {count} 件の新規記事")
        except Exception as e:
            print(f"  [エラー] {e}")

        if i < len(QUERIES) - 1:
            time.sleep(0.5)

    # 日時の降順でソート
    sorted_articles = sorted(
        articles.values(), key=lambda x: x['pubDate'], reverse=True
    )

    oldest_date = sorted_articles[-1]['pubDate'] if sorted_articles else None
    newly_added = len(sorted_articles) - existing_count

    # 保存
    output = {
        'lastUpdated': datetime.now(timezone.utc).isoformat(),
        'oldestDate': oldest_date,
        'count': len(sorted_articles),
        'articles': sorted_articles,
    }

    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
        f.write('\n')

    print(f"\n{'=' * 50}")
    print(f"完了！ 合計 {len(sorted_articles)} 件 (新規 {newly_added} 件追加)")
    print(f"  -> {OUTPUT_PATH}")
    print(f"{'=' * 50}")


if __name__ == '__main__':
    main()
