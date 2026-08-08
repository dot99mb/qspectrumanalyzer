from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


project_root = Path(SPECPATH).parent
package_root = project_root / "qspectrumanalyzer"

datas = [
    (str(package_root / "qspectrumanalyzer.svg"), "qspectrumanalyzer"),
    (str(package_root / "languages"), "qspectrumanalyzer/languages"),
    (str(project_root / "packaging" / "RTL_SDR_TOOLS_RU.txt"), "tools/rtl-sdr"),
]
datas += collect_data_files("pyqtgraph")
hiddenimports = collect_submodules("soapypower")

a = Analysis(
    [str(package_root / "__main__.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PyQt5", "PyQt6", "OpenGL", "pytest", "scipy", "tkinter"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="QSpectrumAnalyzer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(project_root / "qspectrumanalyzer.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="QSpectrumAnalyzer",
)
