# -*- mode: python ; coding: utf-8 -*-
import os

block_cipher = None
datas = []

# ---------------------------------------------------------------------------
# Bundle the web frontend (index.html, css/, js/)
# ---------------------------------------------------------------------------
_spec_dir = os.path.dirname(os.path.abspath(SPEC))
_web_dir = os.path.join(_spec_dir, 'web')
if os.path.isdir(_web_dir):
    datas.append((_web_dir, 'web'))
    print(f"[INFO] Adding web assets: {_web_dir} -> web")
else:
    print("[WARNING] web/ directory not found next to spec file!")

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'webview',
        'webview.platforms.edgechromium',
        'webview.platforms.winforms',
        'clr',
        'pythoncom',
        'win32com',
        'pystray',
        'pystray._win32',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'core.tray',
        'core.autostart',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='API-Monitor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='file_version_info.txt',
    icon='cc_switch_icon.ico',
)