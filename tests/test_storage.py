"""原子保存和并发读改写测试，只使用临时目录。"""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.storage import atomic_update_text, atomic_write_text


def test_replace_failure_preserves_existing_file(tmp_path, monkeypatch):
    """输入：最终替换失败；输出：原文件不变；用途：保证异常不损坏配置；边界：临时文件清理。"""
    path = tmp_path / "config.yaml"
    path.write_text("theme: dark\n", encoding="utf-8")
    monkeypatch.setattr("app.storage.os.replace", lambda *args: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        atomic_write_text(path, "theme: light\n")
    assert path.read_text(encoding="utf-8") == "theme: dark\n"
    assert not list(tmp_path.glob("*.tmp"))


def test_thread_updates_do_not_lose_changes(tmp_path):
    """输入：多个同时修改同文件的线程；输出：所有修改保留；边界：不依赖调用者私有锁。"""
    path = tmp_path / "counter.txt"
    path.write_text("0", encoding="utf-8")
    def increment(_):
        """输入：任务号；输出：新数值文本；用途：原子递增；边界：仅测试文件。"""
        return atomic_update_text(path, lambda text: str(int(text) + 1))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(increment, range(40)))
    assert path.read_text(encoding="utf-8") == "40"


def test_separate_processes_do_not_overwrite_updates(tmp_path):
    """输入：独立进程同时更新配置；输出：全部更新保留；用途：覆盖多个EXE实例；边界：子进程限时回收。"""
    path = tmp_path / "counter.txt"
    path.write_text("0", encoding="utf-8")
    script = "from pathlib import Path; from app.storage import atomic_update_text; import sys; p=Path(sys.argv[1]); [atomic_update_text(p,lambda s:str(int(s)+1)) for _ in range(10)]"
    processes = [subprocess.Popen([sys.executable, "-c", script, str(path)], cwd=str(Path(__file__).resolve().parents[1])) for _ in range(3)]
    try:
        for process in processes:
            assert process.wait(timeout=20) == 0
        assert path.read_text(encoding="utf-8") == "30"
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()


def test_interrupted_writer_preserves_complete_original(tmp_path):
    """输入：在替换前立即退出的进程；输出：旧文件完整；用途：模拟关闭/进程异常；边界：不结束用户程序。"""
    path = tmp_path / "settings.json"
    original = {"theme": "dark", "interval": 120}
    path.write_text(json.dumps(original), encoding="utf-8")
    script = "from pathlib import Path; import os,sys; import app.storage as s; s.os.replace=lambda *args:os._exit(9); s.atomic_write_text(Path(sys.argv[1]),'replacement')"
    result = subprocess.run([sys.executable, "-c", script, str(path)], cwd=str(Path(__file__).resolve().parents[1]), timeout=15)
    assert result.returncode == 9
    assert json.loads(path.read_text(encoding="utf-8")) == original
