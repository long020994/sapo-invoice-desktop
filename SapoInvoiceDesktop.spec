# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

pdf_preview_data = collect_data_files('pypdfium2') + collect_data_files('pypdfium2_raw')
<<<<<<< HEAD
customtkinter_data = collect_data_files('customtkinter')


a = Analysis(
    ['run_app.pyw'],
    pathex=[],
    binaries=[],
    datas=[('sapo_database.json', '.'), ('sapo_import_template.xlsx', '.')] + pdf_preview_data + customtkinter_data,
    hiddenimports=['openai', 'flask', 'flask_cors', 'PIL.ImageTk', 'openpyxl', 'pypdfium2', 'pypdfium2_raw', 'customtkinter', 'darkdetect'],
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
    name='SapoInvoiceDesktop',
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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SapoInvoiceDesktop',
)
=======


a = Analysis(
    ['run_app.pyw'],
    pathex=[],
    binaries=[],
    datas=[('sapo_database.json', '.'), ('sapo_import_template.xlsx', '.')] + pdf_preview_data,
    hiddenimports=['openai', 'flask', 'flask_cors', 'PIL.ImageTk', 'openpyxl', 'pypdfium2', 'pypdfium2_raw'],
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
    name='SapoInvoiceDesktop',
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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SapoInvoiceDesktop',
)
>>>>>>> 67e4a39858858e451c25a340f9b016f48e994b1a
