"""开发目录和打包后的路径。

打包成单文件时，网页和历史缓存打进 exe，启动时由解包目录直接读取。
可写的 config.yaml 放在 exe 旁边，用来记住主题。
"""

from __future__ import annotations

import re
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
    用途：放置 config.yaml 和浏览器数据。
    边界：打包后是 exe 所在目录，源码运行时是项目根目录。
    """
    if frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def bundle_root() -> Path:
    """输入：无。
    输出：只读资源目录。
    用途：读取打包进去的网页、默认配置和历史缓存。
    边界：未打包时与 runtime_root 相同。单文件 exe 里这是启动时的解包目录。
    """
    if frozen():
        return Path(getattr(sys, "_MEIPASS"))
    return runtime_root()


_WINDOWS_BLOCK = re.compile(r"(?m)^crash_windows:\n(?:[ \t]+-[ \t]*\S[^\n]*\n)+")


def cache_file() -> Path:
    """输入：无。
    输出：历史缓存路径。
    用途：启动时读取灰色柱和虚线。
    边界：打包后用解包目录里的那份，不复制到 exe 旁边。源码运行时就是项目里的 cache/baselines.json。
    """
    return bundle_root() / "cache" / "baselines.json"


def prepare_runtime() -> None:
    """输入：无。
    输出：无。
    用途：打包运行时补齐 exe 旁边的配置，让日期段和包内缓存一致，并指定浏览器数据目录。
    边界：源码运行时什么都不做。不把历史缓存复制出来。主题等手改项保留。
    """
    if not frozen():
        return
    import os

    root = runtime_root()
    bundled = bundle_root()
    os.environ["WEBVIEW2_USER_DATA_FOLDER"] = str(root / "webview2")
    install_user_config(bundled / "config.yaml", root / "config.yaml")


def install_user_config(source: Path, target: Path) -> None:
    """输入：发布配置、exe 旁边的配置。
    输出：无。
    用途：只替换日期段列表，避免新缓存和旧日期段对不上。
    边界：目标不存在时整文件复制。找不到日期段块时不改。
    """
    if not source.is_file():
        return
    bundled = source.read_text(encoding="utf-8")
    block = _WINDOWS_BLOCK.search(bundled)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        return
    if block is None:
        return
    local = target.read_text(encoding="utf-8")
    if _WINDOWS_BLOCK.search(local) is None:
        return
    updated = _WINDOWS_BLOCK.sub(block.group(0), local, count=1)
    if updated != local:
        target.write_text(updated, encoding="utf-8")
