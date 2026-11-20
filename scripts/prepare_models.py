"""Stage known OCR weights for local use and the Windows distribution."""

import hashlib
import shutil
import tempfile
from pathlib import Path
from urllib.request import urlopen

BASE = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/PP-OCRv5"
MODELS = {
    "det/ch_PP-OCRv5_det_mobile.onnx": "4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae",
    "rec/ch_PP-OCRv5_rec_mobile.onnx": "5825fc7ebf84ae7a412be049820b4d86d77620f204a041697b0494669b1742c5",
}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    directory = Path(__file__).resolve().parents[1] / "models"
    directory.mkdir(exist_ok=True)
    for asset, digest in MODELS.items():
        target = directory / Path(asset).name
        if target.is_file() and sha256(target) == digest:
            print(f"Verified {target.name}")
            continue
        with tempfile.NamedTemporaryFile(dir=directory, suffix=".download", delete=False) as temp:
            downloaded = Path(temp.name)
        try:
            with urlopen(f"{BASE}/{asset}", timeout=30) as response, downloaded.open("wb") as stream:
                shutil.copyfileobj(response, stream)
            if sha256(downloaded) != digest:
                raise ValueError(f"Checksum mismatch for {target.name}")
            downloaded.replace(target)
        finally:
            downloaded.unlink(missing_ok=True)
        print(f"Prepared {target.name}")


if __name__ == "__main__":
    main()
