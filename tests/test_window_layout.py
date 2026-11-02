"""工作区居中几何验证，不创建原生窗口。"""

import pytest

from app.window_layout import centered_bounds


def test_center_uses_work_area_and_physical_size():
    """输入：125% DPI 实际窗口与工作区；输出：居中边界；边界：排除底部任务栏。"""
    assert centered_bounds(1100, 800, (0, 0, 1920, 1040)) == (410, 120, 1100, 800)


def test_negative_monitor_coordinates_and_oversized_window():
    """输入：左侧显示器和超大窗口；输出：比例缩小后的边界；边界：不超工作区。"""
    assert centered_bounds(880, 640, (-1920, 0, 0, 1040)) == (-1400, 200, 880, 640)
    assert centered_bounds(1760, 1280, (0, 0, 1280, 800)) == (90, 0, 1100, 800)
    with pytest.raises(ValueError):
        centered_bounds(0, 640, (0, 0, 1920, 1040))
