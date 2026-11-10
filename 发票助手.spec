# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 —— 本地构建与 GitHub Actions 共用。

产物是 onedir（dist/发票助手/），分发时压成 zip：
  --onefile 每次启动都要把上百 MB 解压到 %TEMP%，在限制了临时目录权限的机器上
  会直接失败（Could not create temporary directory），冷启动也要十几秒；
  onedir 双击即开，用户看到的就是「一个文件夹 + 一个 exe」。
"""
from PyInstaller.utils.hooks import collect_submodules

hidden = ['pymupdf', 'lxml'] + collect_submodules('openpyxl')

a = Analysis(
    ['src/app_entry.py'],
    pathex=['src'],
    binaries=[],
    datas=[('web', 'web')],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy', 'scipy', 'pandas', 'matplotlib', 'tkinter', 'pytest'],
    noarchive=False,
)

# 只用到文字层提取，PyMuPDF 自带的版面分析 ONNX 模型（约 33MB）用不上
a.datas = [d for d in a.datas if not d[0].lower().endswith('.onnx')]
a.binaries = [b for b in a.binaries if not b[0].lower().endswith('.onnx')]

pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='发票助手',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False, upx_exclude=[],
    name='发票助手',
)
