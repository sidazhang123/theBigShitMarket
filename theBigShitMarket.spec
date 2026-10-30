# -*- mode: python ; coding: utf-8 -*-
"""单文件、无控制台。网页、默认配置和历史缓存打进 exe。"""

import PyInstaller.lib.modulegraph.modulegraph as mg

_orig_scan_bytecode = mg.ModuleGraph._scan_bytecode


def _scan_bytecode(self, module, module_code_object, is_scanning_imports):
    """输入：模块节点、代码对象、是否扫描 import。
    输出：原扫描结果；本机 Python 3.10.0 拆不了个别大文件时跳过字节码扫描。
    用途：让打包能越过 bottle 的反汇编错误。语法树里的 import 已经扫过。
    边界：只吞 IndexError。
    """
    try:
        return _orig_scan_bytecode(self, module, module_code_object, is_scanning_imports)
    except IndexError:
        return None


mg.ModuleGraph._scan_bytecode = _scan_bytecode

a = Analysis(
    ["app/__main__.py"],
    pathex=["."],
    binaries=[],
    datas=[
        ("web", "web"),
        ("config.yaml", "."),
        ("cache/baselines.json", "cache"),
    ],
    hiddenimports=["clr", "webview.platforms.edgechromium", "webview.platforms.winforms"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["webview.platforms.android"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="theBigShitMarket",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
)
