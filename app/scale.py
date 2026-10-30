"""家数到像素的比例。

>6% 灰柱的半宽固定为配置里的像素。其余档、实时柱和虚线共用这个比例。
虚线再乘一次内缩。灰柱的内缩系数用 1。
"""

from __future__ import annotations


def half_px(count: float, base_shallow: float, half_width: float, inset: float = 1.0) -> int:
    """输入：家数、>6% 基底家数、半宽像素、内缩系数。
    输出：中轴到这一档端点的像素，四舍五入到整数。
    用途：灰柱半宽与虚线半宽使用同一把尺子。
    边界：基底、家数、半宽或内缩小于等于 0 时返回 0，不除零。
    """
    if base_shallow <= 0 or count <= 0 or half_width <= 0 or inset <= 0:
        return 0
    return int(round(float(count) * float(inset) * float(half_width) / float(base_shallow)))
