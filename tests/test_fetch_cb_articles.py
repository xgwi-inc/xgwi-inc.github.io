from fetch_cb_articles import is_cb_rate_article


class TestIsCbRateArticle:
    def test_boj_rate_hike_matches(self):
        assert is_cb_rate_article("日銀が利上げ決定、0.5%に引き上げ")

    def test_fed_rate_cut_matches(self):
        assert is_cb_rate_article("FRBが利下げ、政策金利を引き下げ")

    def test_ecb_hold_matches(self):
        assert is_cb_rate_article("ECBが金利据え置き、ラガルド総裁発言")

    def test_boe_matches(self):
        assert is_cb_rate_article("英中銀ベイリー総裁、利下げ示唆")

    def test_rba_matches(self):
        assert is_cb_rate_article("豪中銀、政策金利を据え置き")

    def test_boc_matches(self):
        assert is_cb_rate_article("カナダ中銀マックレム総裁、利下げ継続")

    def test_imf_matches(self):
        assert is_cb_rate_article("IMF、世界の金利動向を分析")

    def test_regional_fed_matches(self):
        assert is_cb_rate_article("連銀ウィリアムズ総裁、金利見通しを議論")

    def test_no_cb_keyword_rejected(self):
        assert not is_cb_rate_article("日本政府、利上げ対策を発表")

    def test_no_rate_keyword_rejected(self):
        assert not is_cb_rate_article("日銀植田総裁、経済の先行きを語る")

    def test_unrelated_rejected(self):
        assert not is_cb_rate_article("米国株式市場、S&P500が最高値更新")

    def test_empty_string_rejected(self):
        assert not is_cb_rate_article("")
