"""涨幅门槛计数。"""

import numpy as np

from app.count import THRESHOLDS, count_tiers


def test_strict_cumulative_counts():
    """恰好落在门槛上的涨幅不计入该档，更深的档同时计入更浅的档。"""
    pct = np.array([-16.0, -16.01, -6.0, -6.01, -5.0, np.nan, np.inf])
    counts = count_tiers(pct)
    assert list(THRESHOLDS) == [16, 15, 14, 13, 12, 11, 10, 9, 8, 7, 6]
    assert int(counts[0]) == 1
    assert int(counts[-1]) == 3
    assert all(int(counts[i]) <= int(counts[i + 1]) for i in range(10))


def test_empty_and_non_finite():
    """没有可用涨幅时 11 档都是 0，且不改写入参。"""
    original = np.array([np.nan, np.inf])
    snapshot = original.copy()
    assert list(count_tiers(original)) == [0] * 11
    assert list(count_tiers(np.array([]))) == [0] * 11
    assert np.array_equal(original, snapshot, equal_nan=True)
