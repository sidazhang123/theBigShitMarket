"""开发目录和打包后的路径。

打包成单文件时，网页资源在解压目录里，配置和缓存放在 exe 旁边，方便改写。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def frozen() -> bool:
    """输入：无。
    输出：当前是否在 PyInstaller 单文件里运行。
    用途：区分源码启动和 exe 启动。
    边界：只看解释器标记，不检查文件是否存在。
    """
    return bool(getattr(sys, "frozen", False))


def runtime_root() -> Path:
    """输入：无。
    输出：可写的程序目录。
    用途：放置 config.yaml 和缓存。
    边界：打包后是 exe 所在目录，源码运行时是项目根目录。
    """
    if frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def bundle_root() -> Path:
    """输入：无。
    输出：只读资源目录。
    用途：读取打包进去的网页和默认配置。
    边界：未打包时与 runtime_root 相同。
    """
    if frozen():
        return Path(getattr(sys, "_MEIPASS"))
    return runtime_root()


def prepare_runtime() -> None:
    """输入：无。
    输出：无。
    用途：打包运行时把缺的配置和历史缓存复制到 exe 旁边，并指定浏览器数据目录。
    边界：源码运行时什么都不做。已有文件不覆盖。
    """
    if not frozen():
        return
    import os

    root = runtime_root()
    bundled = bundle_root()
    os.environ["WEBVIEW2_USER_DATA_FOLDER"] = str(root / "webview2")
    _copy_if_missing(bundled / "config.yaml", root / "config.yaml")
    _copy_if_missing(bundled / "cache" / "baselines.json", root / "cache" / "baselines.json")


def _copy_if_missing(source: Path, target: Path) -> None:
    """输入：打包内的源文件、exe 旁边的目标文件。
    输出：无。
    用途：首次运行补齐可写文件。
    边界：源文件不存在或目标已存在时跳过。
    """
    if target.exists() or not source.is_file():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
