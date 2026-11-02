"""开发目录和打包后的路径。

打包成单文件时，网页和历史缓存打进 exe，启动时由解包目录直接读取。
可写的 config.yaml 放在 exe 旁边，用来记住主题、刷新周期和行情地址顺序。
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from app.storage import atomic_update_text


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
    用途：日期段与包内缓存对齐，并将单行情地址转换为有序地址列表。
    边界：目标不存在时整文件复制。已有地址列表、主题和刷新周期保留。
    """
    if not source.is_file():
        return
    bundled = source.read_text(encoding="utf-8")
    defaults = yaml.safe_load(bundled)
    if not isinstance(defaults, dict):
        raise ValueError("发布配置顶层必须为映射")

    def transform(local: str) -> str:
        """输入：锁内用户配置；输出：日期对齐后的配置；边界：缺文件复制完整发布文本。"""
        if not local:
            return bundled
        settings = yaml.safe_load(local)
        if not isinstance(settings, dict):
            raise ValueError("用户配置顶层必须为映射")
        updates = {}
        if "crash_windows" in defaults:
            updates["crash_windows"] = defaults["crash_windows"]
        if "quote_hosts" not in settings and defaults.get("quote_hosts"):
            hosts = []
            if settings.get("quote_host"):
                hosts.append({"host": str(settings["quote_host"]), "port": int(settings.get("quote_port", 7709))})
            for item in defaults["quote_hosts"]:
                if item not in hosts:
                    hosts.append(item)
            updates["quote_hosts"] = hosts
        for key in ("quote_deadline_seconds", "quote_min_stocks", "quote_min_coverage"):
            if key not in settings and key in defaults:
                updates[key] = defaults[key]
        return _edit_top_level(local, updates, ("quote_host", "quote_port") if "quote_hosts" in settings or "quote_hosts" in updates else ())

    atomic_update_text(target, transform)


def _edit_top_level(text: str, updates: dict, remove: tuple[str, ...]) -> str:
    """输入：YAML 文本、替换值和删除键；输出：完整新文本。
    用途：按根映射节点范围更新，支持注释、空行、行内列表和无末尾换行。
    边界：已有非目标节点原样保留；重复键拒绝；不在此处校验业务参数。
    """
    node = yaml.compose(text)
    if not isinstance(node, yaml.MappingNode):
        raise ValueError("配置顶层必须为映射")
    if node.flow_style:
        mapping = yaml.safe_load(text)
        if len(mapping) != len(node.value):
            raise ValueError("配置包含重复键")
        for key in remove:
            mapping.pop(key, None)
        mapping.update(updates)
        return yaml.safe_dump(mapping, allow_unicode=True, sort_keys=False)
    seen = set()
    edits = []
    for index, (key_node, value_node) in enumerate(node.value):
        key = key_node.value
        if key in seen:
            raise ValueError(f"配置包含重复键：{key}")
        seen.add(key)
        if key not in updates and key not in remove:
            continue
        start = key_node.start_mark.index
        end = node.value[index + 1][0].start_mark.index if index + 1 < len(node.value) else len(text)
        if key in remove:
            # 删除单行标量时保留后续独立注释，不把它们当成被删除字段的一部分。
            value_end = max(start, value_node.end_mark.index)
            newline = text.find("\n", value_end)
            end = min(end, newline + 1 if newline >= 0 else len(text))
        replacement = yaml.safe_dump({key: updates[key]}, allow_unicode=True, sort_keys=False) if key in updates else ""
        edits.append((start, end, replacement))
    for start, end, replacement in reversed(edits):
        text = text[:start] + replacement + text[end:]
    missing = {key: value for key, value in updates.items() if key not in seen}
    if missing:
        text = text.rstrip("\n") + "\n" + yaml.safe_dump(missing, allow_unicode=True, sort_keys=False)
    return text
