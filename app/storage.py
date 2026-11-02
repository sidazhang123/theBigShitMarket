"""本地文本的原子保存与跨进程读改写，防止退出中断和多窗口覆盖设置。"""

from __future__ import annotations

import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

_GUARD = threading.Lock()
_LOCKS: dict[str, threading.Lock] = {}


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    """输入：目标路径；输出：持锁上下文。
    用途：串行化线程及独立进程的配置读改写。
    边界：锁文件保留以避免删除竞争；进程退出由操作系统释放文件锁。
    """
    key = str(path.resolve()).casefold() if os.name == "nt" else str(path.resolve())
    with _GUARD:
        lock = _LOCKS.setdefault(key, threading.Lock())
    with lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.with_name(f".{path.name}.lock").open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_write_text(path: Path, text: str) -> None:
    """输入：文件路径与完整文本；输出：无。
    用途：写完并同步临时文件后原子替换目标，读者只能看到完整版本。
    边界：替换失败保留旧文件；临时文件与目标位于同一目录，编码固定 UTF-8。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_update_text(path: Path, transform: Callable[[str], str]) -> str:
    """输入：路径和纯文本变换；输出：保存后的完整文本。
    用途：持锁读取最新文件再修改，避免独立窗口丢失彼此的设置。
    边界：缺文件按空串传入；变换或写入失败不替换旧文件；无变化不重写。
    """
    path = Path(path)
    with _file_lock(path):
        original = path.read_text(encoding="utf-8") if path.exists() else ""
        updated = transform(original)
        if updated != original or not path.exists():
            atomic_write_text(path, updated)
        return updated
