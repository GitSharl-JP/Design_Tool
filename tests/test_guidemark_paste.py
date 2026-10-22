from app.engines.guidemark_paste import parse_labels


def test_plain_numbers_any_separator():
    assert parse_labels("1, 5 7;9\n11\t12、13") == [
        ["1"], ["5"], ["7"], ["9"], ["11"], ["12"], ["13"]]


def test_suffixed_number_becomes_two_runs():
    assert parse_labels("14-1") == [["14", "-1"]]
    assert parse_labels("14a 14-A") == [["14", "a"], ["14", "-A"]]


def test_duplicates_and_order_are_kept():
    assert parse_labels("55 1 55") == [["55"], ["1"], ["55"]]


def test_unicode_dashes_and_fullwidth_digits():
    assert parse_labels("14–1 １４−1") == [
        ["14", "-1"], ["14", "-1"]]


def test_non_numeric_tokens_stay_single_run():
    assert parse_labels("A12 EX-3") == [["A12"], ["EX-3"]]


def test_ranges_are_not_expanded_here():
    # "1-15" is read as label "1" + "-15"; ranges belong to the Numbers field
    assert parse_labels("1-15") == [["1", "-15"]]


def test_empty_and_blank():
    assert parse_labels("") == []
    assert parse_labels(None) == []
    assert parse_labels("  \n\t, ; ") == []
