"""One-file Windows build of Guild Tracker."""

from pathlib import Path
from importlib.util import find_spec
import runpy
import os

from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.win32.versioninfo import (
    VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct,
    VarFileInfo, VarStruct,
)


root = Path.cwd()
version = runpy.run_path(str(root / "app_version.py"))
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=version["VERSION_TUPLE"], prodvers=version["VERSION_TUPLE"],
                     mask=0x3F, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable("040904B0", [
        StringStruct("FileDescription", "Guild Tracker for Girls' Frontline 2"),
        StringStruct("FileVersion", version["VERSION"]),
        StringStruct("ProductName", "Guild Tracker"),
        StringStruct("ProductVersion", version["VERSION"]),
        StringStruct("OriginalFilename", "GuildTracker.exe"),
    ])]), VarFileInfo([VarStruct("Translation", [1033, 1200])])],
)
datas = [
    (str(root / "VERSION"), "."),
    (str(root / "GuildBossToolkit.ico"), "."),
    (str(root / "assets" / "badges"), "assets/badges"),
    (str(Path(find_spec("ddddocr").origin).parent / "common_old.onnx"), "ddddocr"),
]
datas += collect_data_files("rapidocr_onnxruntime")

# ONNX and Qt must load the same current Microsoft C++ runtime. PATH can
# contain older copies from Java, and Qt ships its own older runtime copies.
system32 = Path(os.environ["SystemRoot"]) / "System32"
runtime_names = ["vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll",
                 "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_atomic_wait.dll",
                 "msvcp140_codecvt_ids.dll", "concrt140.dll"]
runtimes = {name: system32 / name for name in runtime_names}
for runtime in runtimes.values():
    if not runtime.is_file():
        raise RuntimeError(f"Install the current Microsoft Visual C++ x64 Redistributable: missing {runtime.name}")

analysis = Analysis(
    [str(root / "main.py")],
    pathex=[str(root)],
    binaries=[(str(path), ".") for path in runtimes.values()],
    datas=datas,
    hiddenimports=["numpy._core._exceptions"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib"],
    noarchive=False,
    optimize=0,
)

# The application only reads still images; OpenCV's video codec is unused.
# Some launcher environments add their own DLL directories to PATH. Those
# unrelated DLLs can be picked up by PyInstaller and shadow Qt's dependencies.
analysis.binaries = [
    entry for entry in analysis.binaries
    if "opencv_videoio_ffmpeg" not in entry[0].lower()
    and "codex-runtimes" not in entry[1].lower()
]
analysis.binaries = [
    (name, str(runtimes[Path(name).name.lower()]), kind)
    if Path(name).name.lower() in runtimes else (name, source, kind)
    for name, source, kind in analysis.binaries
]

pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="GuildTracker",
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
    icon=str(root / "GuildBossToolkit.ico"),
    version=version_info,
)
