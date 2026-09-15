# PyInstaller spec: one --onedir folder with two executables sharing one runtime.
#
#   Nova.exe      no console: tray / window mode, what autostart launches
#   nova-cli.exe  console: --text mode, `sysindex ...`, `autostart ...`
#
# Build with packaging\build.ps1 (it also builds the canvas and copies config.toml).
# --onedir instead of --onefile: onefile unpacks ~hundreds of MB to %TEMP% on every
# launch, which would dominate cold start.

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent

datas = [
    (str(ROOT / "assistant" / "ui" / "canvas_dist"), "assistant/ui/canvas_dist"),
    (str(ROOT / "assistant" / "ui" / "nova.ico"), "assistant/ui"),
    (str(ROOT / "AGENTS.md"), "."),
    (str(ROOT / "CLAUDE.md"), "."),
]
datas += collect_data_files("faster_whisper")      # Silero VAD model used by vad_filter
datas += collect_data_files("speech_recognition")  # FLAC encoder for the Google recognizer
datas += collect_data_files("webview")             # WebView2 interop DLLs
datas += collect_data_files("playwright")          # Playwright's driver (browser steps in automations)

binaries = collect_dynamic_libs("ctranslate2")

hiddenimports = (
    collect_submodules("assistant")                # our own modules, including lazily imported ones
    + collect_submodules("uvicorn")                # uvicorn picks loops/protocols by name at runtime
    + collect_submodules("websockets")
    + collect_submodules("rich._unicode_data")
    + collect_submodules("pywinauto")              # UI Automation for desktop-app steps
    + ["pystray._win32", "pyttsx3.drivers", "pyttsx3.drivers.sapi5", "comtypes.client", "clr_loader", "pythonnet"]
)

# Installed in this environment but never used by Nova; keep them out of the bundle.
# The big chain starts at ctranslate2.converters -> transformers -> accelerate / peft /
# datasets -> pyarrow, faiss, spacy... ctranslate2 imports `converters` at startup, so it
# must stay; it guards its transformers/torch imports with try/except, so those can go.
excludes = [
    "transformers", "accelerate", "peft", "datasets", "bitsandbytes", "timm",
    "optuna", "librosa", "numba", "llvmlite", "spacy", "thinc", "pyarrow", "faiss", "openai", "babel",
    "torch", "torchvision", "torchaudio", "tensorflow", "matplotlib", "scipy", "pandas", "sklearn",
    "IPython", "ipywidgets", "jupyter", "notebook", "tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6",
    "cv2", "sympy",
]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

icon = str(ROOT / "assistant" / "ui" / "nova.ico")

nova = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Nova",
    console=False,
    icon=icon,
)
nova_cli = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="nova-cli",
    console=True,
    icon=icon,
)

coll = COLLECT(
    nova, nova_cli,
    a.binaries, a.datas,
    name="Nova",
)
