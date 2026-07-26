# -*- mode: python ; coding: utf-8 -*-
import os

block_cipher = None
datas = []

# ---------------------------------------------------------------------------
# Bundle the web frontend — whitelist only (web/ also contains design
# prototypes, screenshots and helper scripts that must NOT ship in the exe)
# ---------------------------------------------------------------------------
_spec_dir = os.path.dirname(os.path.abspath(SPEC))
_web_dir = os.path.join(_spec_dir, 'web')
if os.path.isdir(_web_dir):
    datas.append((os.path.join(_web_dir, 'index.html'), 'web'))
    datas.append((os.path.join(_web_dir, 'css', 'style.css'), 'web/css'))
    datas.append((os.path.join(_web_dir, 'js', 'app.js'), 'web/js'))
    _bg_dir = os.path.join(_web_dir, 'assets', 'metric-bg')
    if os.path.isdir(_bg_dir):
        for _f in os.listdir(_bg_dir):
            if _f.lower().endswith(('.gif', '.png', '.webp')) and not _f.startswith('_'):
                datas.append((os.path.join(_bg_dir, _f), 'web/assets/metric-bg'))
    print(f"[INFO] Adding whitelisted web assets from: {_web_dir}")
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
    excludes=[
        'tkinter',
        'unittest',
        'pydoc',
        'doctest',
        'test',
        'xmlrpc',
        'requests',
        'urllib3',
        'certifi',
        'charset_normalizer',
        'idna',
    ],
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