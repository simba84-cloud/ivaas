import pytest

from ivaas_ml.standard_check import exactly_n, wilson


def test_counts_accepts_and_rejects():
    truth = [14, 14, 14, 12, 8, 13]
    pred = [14, 13, 14, 14, 8, 13]
    r = exactly_n(truth, pred, 14)
    assert (r["tp"], r["fn"], r["fp"], r["tn"]) == (2, 1, 1, 2)
    assert r["positives"] == 3 and r["negatives"] == 3 and r["samples"] == 6
    assert r["accuracy"] == pytest.approx(4 / 6)
    assert r["false_accept_rate"] == pytest.approx(1 / 3)
    assert r["false_reject_rate"] == pytest.approx(1 / 3)


def test_rate_without_samples_is_none_not_zero():
    r = exactly_n([8, 9], [8, 9], 14)
    assert r["positives"] == 0 and r["false_reject_rate"] is None
    assert r["false_accept_rate"] == 0.0 and r["accuracy"] == 1.0
    assert exactly_n([], [], 14)["accuracy"] is None


def test_lengths_must_match():
    with pytest.raises(ValueError):
        exactly_n([14], [], 14)


def test_wilson_interval_brackets_the_rate_and_widens_when_small():
    lo, hi = wilson(4, 35)
    assert lo < 4 / 35 < hi
    assert wilson(0, 10)[0] == 0.0 and wilson(10, 10)[1] == 1.0
    assert (wilson(1, 10)[1] - wilson(1, 10)[0]) > (wilson(10, 100)[1] - wilson(10, 100)[0])
    assert wilson(0, 0) is None
