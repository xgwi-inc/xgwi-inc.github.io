from update_data import (
    build_arrays,
    common_labels,
    round1,
    to_label,
    trim_labels,
)


class TestRound1:
    def test_rounds_to_one_decimal(self):
        assert round1(2.345) == 2.3  # banker's rounding

    def test_rounds_up(self):
        assert round1(2.36) == 2.4

    def test_integer_passthrough(self):
        assert round1(3) == 3.0

    def test_negative_number(self):
        assert round1(-1.25) == -1.2  # banker's rounding


class TestToLabel:
    def test_iso_date_trims_to_year_month(self):
        assert to_label("2024-01-15") == "2024-01"

    def test_year_month_only_unchanged(self):
        assert to_label("2024-01") == "2024-01"


class TestCommonLabels:
    def test_union_sorted(self):
        a = {"2016-01": 1.0, "2016-02": 2.0}
        b = {"2016-02": 3.0, "2016-03": 4.0}
        assert common_labels(a, b) == ["2016-01", "2016-02", "2016-03"]

    def test_filters_before_2016(self):
        a = {"2015-12": 1.0, "2016-01": 2.0}
        assert common_labels(a) == ["2016-01"]

    def test_handles_none_and_empty(self):
        assert common_labels(None, {}, {"2016-01": 1.0}) == ["2016-01"]

    def test_all_empty_returns_empty(self):
        assert common_labels() == []


class TestBuildArrays:
    def test_aligns_values_to_labels(self):
        labels = ["2016-01", "2016-02", "2016-03"]
        a = {"2016-01": 1.0, "2016-03": 3.0}
        b = {"2016-02": 2.0}
        arr_a, arr_b = build_arrays(labels, a, b)
        assert arr_a == [1.0, None, 3.0]
        assert arr_b == [None, 2.0, None]

    def test_empty_labels_yields_empty_arrays(self):
        arr_a, arr_b = build_arrays([], {"x": 1}, {"y": 2})
        assert arr_a == []
        assert arr_b == []


class TestTrimLabels:
    def test_trims_trailing_all_none(self):
        labels = ["2016-01", "2016-02", "2016-03"]
        a = [1.0, 2.0, None]
        b = [1.0, 2.0, None]
        new_labels, (new_a, new_b) = trim_labels(labels, a, b)
        assert new_labels == ["2016-01", "2016-02"]
        assert new_a == [1.0, 2.0]
        assert new_b == [1.0, 2.0]

    def test_keeps_row_if_any_value_present(self):
        labels = ["2016-01", "2016-02"]
        a = [1.0, None]
        b = [1.0, 2.0]
        new_labels, (new_a, new_b) = trim_labels(labels, a, b)
        assert new_labels == ["2016-01", "2016-02"]
        assert new_a == [1.0, None]
        assert new_b == [1.0, 2.0]

    def test_trims_multiple_trailing_rows(self):
        labels = ["2016-01", "2016-02", "2016-03", "2016-04"]
        a = [1.0, 2.0, None, None]
        b = [1.0, 2.0, None, None]
        new_labels, (new_a, new_b) = trim_labels(labels, a, b)
        assert new_labels == ["2016-01", "2016-02"]
        assert new_a == [1.0, 2.0]
        assert new_b == [1.0, 2.0]

    def test_empty_labels_safe(self):
        new_labels, arrays = trim_labels([])
        assert new_labels == []
        assert arrays == ()
