# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = []
for package in (
    "bcrypt", "reportlab", "PIL", "openpyxl", "qrcode", "cryptography",
    "google.oauth2", "googleapiclient", "google_auth_oauthlib", "tkinterweb",
):
    hiddenimports += collect_submodules(package)

datas = [
    ("assets", "assets"),
    ("receipts", "receipts"),
    ("reports", "reports"),
    ("backups", "backups"),
    ("assets/help", "assets/help"),
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SFMS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="SFMS",
)
