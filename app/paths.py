"""开发目录和打包后的路径。

打包成单文件时，网页资源在解压目录里，配置和缓存放在 exe 旁边，方便改写。
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


_WINDOWS_BLOCK = re.compile(r"(?m)^crash_windows:\n(?:[ \t]+-[ \t]*\S[^\n]*\n)+")


def prepare_runtime() -> None:
    """输入：无。
    输出：无。
    用途：打包运行时把历史缓存和日期段同步到 exe 旁边，并指定浏览器数据目录。
    边界：源码运行时什么都不做。主题等手改项保留，缓存按发布内容覆盖。
    """
    if not frozen():
        return
    import os

    root = runtime_root()
    bundled = bundle_root()
    os.environ["WEBVIEW2_USER_DATA_FOLDER"] = str(root / "webview2")
    refresh_shipped_data(bundled, root)


def refresh_shipped_data(bundled: Path, root: Path) -> None:
    """输入：打包资源目录、exe 旁边的可写目录。
    输出：无。
    用途：用这次发布的历史缓存替换旧缓存，并让日期段列表与缓存一致。
    边界：配置里的主题和其他手改项保留。两边内容相同则不写。
    """
    _replace_if_different(bundled / "cache" / "baselines.json", root / "cache" / "baselines.json")
    _sync_crash_windows(bundled / "config.yaml", root / "config.yaml")


def _replace_if_different(source: Path, target: Path) -> None:
    """输入：发布文件、可写副本。
    输出：无。
    用途：缓存更新后，已有的旧副本也换成新内容。
    边界：源文件不存在或两边字节相同则跳过。
    """
    if not source.is_file():
        return
    payload = source.read_bytes()
    if target.is_file() and target.read_bytes() == payload:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)


def _sync_crash_windows(source: Path, target: Path) -> None:
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
