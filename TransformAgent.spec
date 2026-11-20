from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

project = Path(SPECPATH)
ocr_data = collect_data_files("rapidocr", excludes=["models/**"])
ocr_weights = [(str(path), "models") for path in (project / "models").glob("*.onnx")]
if len(ocr_weights) != 2:
    raise SystemExit("Run scripts/prepare_models.py before building.")

analysis = Analysis(
    [str(project / "src" / "transformagent" / "app.py")],
    pathex=[str(project / "src")],
    datas=ocr_data + ocr_weights,
    hiddenimports=[
        "rapidocr.main", "rapidocr.utils.typings", "rapidocr.inference_engine.onnxruntime.main",
        "keyring.backends.Windows",
    ],
    excludes=["tkinter", "torch", "paddle", "tensorflow", "scipy"],
    noarchive=False,
)
# Qt uses the ICU ABI supplied by Windows 11. A build shell may put another
# program's incompatible ICU DLL on PATH; let Windows supply its own copy.
# OpenCV's video codec plugin is also unnecessary for OCR of pixel arrays.
analysis.binaries = [
    entry for entry in analysis.binaries
    if not Path(entry[0]).name.lower().startswith(("opencv_videoio_ffmpeg", "icudt"))
    and Path(entry[0]).name.lower() not in {"icu.dll", "icuin.dll", "icuuc.dll"}
]
archive = PYZ(analysis.pure)
program = EXE(archive, analysis.scripts, [], exclude_binaries=True, name="TransformAgent", console=False)
distribution = COLLECT(program, analysis.binaries, analysis.datas, name="TransformAgent", upx=False)
