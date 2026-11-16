# PyInstaller spec: builds dist/DesktopWaifu/DesktopWaifu.exe (one folder, no console).
#   .venv\Scripts\python -m PyInstaller installer\DesktopWaifu.spec --noconfirm
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent

datas = [(str(ROOT / "assets"), "assets")]
for pkg in ("soundcard", "certifi", "edge_tts", "google.genai"):
    datas += collect_data_files(pkg)

hidden = (collect_submodules("waifu") + collect_submodules("OpenGL.platform") + collect_submodules("OpenGL.arrays")
          + collect_submodules("pyttsx3.drivers") + collect_submodules("google.genai")
          + ["OpenGL.GL", "OpenGL.GL.shaders", "comtypes.client", "pythoncom", "pywintypes", "soundcard.mediafoundation"])

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "PyQt5", "PyQt6", "PySide6.Qt3DCore", "PySide6.QtWebEngineCore",
              "PySide6.QtWebEngineWidgets", "PySide6.QtQuick3D", "PySide6.QtCharts", "PySide6.QtDataVisualization",
              "PySide6.QtPdf", "PySide6.QtDesigner", "PySide6.QtBluetooth", "PySide6.QtLocation", "PySide6.QtPositioning"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="DesktopWaifu",
    icon=str(ROOT / "assets" / "icon.ico"),
    console=False,
    version=str(ROOT / "installer" / "version_info.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="DesktopWaifu")
