"""窗口首次显示时按实际物理尺寸在工作区居中，不干预后续拖动缩放。"""

from __future__ import annotations

import os
from typing import Any


def centered_bounds(
    width: int, height: int, work_area: tuple[int, int, int, int]
) -> tuple[int, int, int, int]:
    """输入：窗口物理宽高和工作区 left/top/right/bottom。
    输出：居中后的 x、y、宽、高。
    用途：避开任务栏，超大窗口按比例缩小到工作区内。
    边界：支持负坐标显示器；工作区或窗口无效时抛出 ValueError。
    """
    left, top, right, bottom = work_area
    available_w, available_h = right - left, bottom - top
    if min(width, height, available_w, available_h) <= 0:
        raise ValueError("窗口和工作区宽高必须为正数")
    scale = min(1.0, available_w / width, available_h / height)
    width, height = int(width * scale), int(height * scale)
    return left + (available_w - width) // 2, top + (available_h - height) // 2, width, height


def center_window(window: Any, client_size: tuple[int, int] | None = None) -> None:
    """输入：已显示的窗口和可选的逻辑客户区宽高；输出：无。
    用途：按 DPI 和标题栏尺寸设置实际客户区，在工作区居中。
    边界：仅首次 shown 事件调用；其它平台保留框架默认居中，API 失败不挪动窗口。
    """
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    class MonitorInfo(ctypes.Structure):
        """Win32 显示器边界和排除任务栏后的工作区。"""
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    user32.GetDpiForWindow.restype = wintypes.UINT
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = wintypes.LONG
    user32.AdjustWindowRectExForDpi.argtypes = [
        ctypes.POINTER(wintypes.RECT), wintypes.DWORD, wintypes.BOOL,
        wintypes.DWORD, wintypes.UINT,
    ]
    user32.AdjustWindowRectExForDpi.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    hwnd = int(window.native.Handle.ToInt64())
    info = MonitorInfo()
    info.cbSize = ctypes.sizeof(info)
    bounds = wintypes.RECT()
    monitor = user32.MonitorFromWindow(hwnd, 2)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return
    if not user32.GetWindowRect(hwnd, ctypes.byref(bounds)):
        return
    width, height = bounds.right - bounds.left, bounds.bottom - bounds.top
    if client_size is not None:
        dpi = user32.GetDpiForWindow(hwnd) or 96
        requested = wintypes.RECT(0, 0, round(client_size[0] * dpi / 96), round(client_size[1] * dpi / 96))
        if user32.AdjustWindowRectExForDpi(
            ctypes.byref(requested), user32.GetWindowLongW(hwnd, -16), False,
            user32.GetWindowLongW(hwnd, -20), dpi,
        ):
            width, height = requested.right - requested.left, requested.bottom - requested.top
    work = info.rcWork
    x, y, width, height = centered_bounds(
        width, height,
        (work.left, work.top, work.right, work.bottom),
    )
    user32.SetWindowPos(hwnd, None, x, y, width, height, 0x0004 | 0x0010)
