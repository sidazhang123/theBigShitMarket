"""11 档累计跌幅家数。

实时行情和测试共用这一处计数，避免两套门槛。
只接受已经换算成百分点的涨幅，不读取行情。
"""

from __future__ import annotations

import numpy as np

# 从深到浅。与画面从上到下的档位一致。
THRESHOLDS: tuple[int, ...] = (16, 15, 14, 13, 12, 11, 10, 9, 8, 7, 6)
_LIMITS = -np.asarray(THRESHOLDS, dtype=np.float64)


def _finite_pct(pct: np.ndarray) -> np.ndarray:
    """输入：涨幅百分点数组。
    输出：只含有限值的一维数组。
    用途：计数前丢掉停牌和空值。
    边界：输入不是数组时会先转成 float64。不修改原数组。
    """
    values = np.asarray(pct, dtype=np.float64).ravel()
    return values[np.isfinite(values)]


def summarize_pct(pct: np.ndarray) -> tuple[list[int], int]:
    """输入：涨幅百分点数组，允许非有限值。
    输出：11 档家数，以及有限涨幅的股票数。
    用途：实时轮询只走这一次筛选和排序。
    边界：没有有限值时家数为 0，股票数为 0。
    """
    finite = _finite_pct(pct)
    if finite.size == 0:
        return [0] * len(THRESHOLDS), 0
    counts = np.searchsorted(np.sort(finite), _LIMITS, side="left")
    return [int(item) for item in counts], int(finite.size)


def count_tiers(pct: np.ndarray) -> np.ndarray:
    """输入：涨幅百分点的一维数组，允许 NaN 和非有限值。
    输出：长度 11 的 int64，顺序与 THRESHOLDS 相同。每个值是涨幅严格小于该门槛负值的家数。
    用途：把一帧全市场涨幅收成累计档位。
    边界：空数组或全非有限时返回全 0。不修改调用方传入的数组。
    """
    counts, _ = summarize_pct(pct)
    return np.asarray(counts, dtype=np.int64)
