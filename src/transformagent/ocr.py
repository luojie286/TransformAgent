"""Local CPU OCR, held on one worker thread and warmed once."""

from pathlib import Path
import sys

import cv2
import numpy as np
from PySide6.QtCore import QObject, Signal, Slot

MODEL_NAMES = ("ch_PP-OCRv5_det_mobile.onnx", "ch_PP-OCRv5_rec_mobile.onnx")


def model_directory() -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return root / "models"


class Recognizer:
    def __init__(self, directory: Path):
        from rapidocr import EngineType, LangDet, LangRec, ModelType, OCRVersion, RapidOCR

        detector, recognition = (directory / name for name in MODEL_NAMES)
        if not detector.is_file() or not recognition.is_file():
            raise FileNotFoundError("缺少 OCR 模型，请先运行项目的模型准备命令。")
        self.engine = RapidOCR(params={
            "Global.use_cls": False,
            "Global.log_level": "warning",
            "Det.engine_type": EngineType.ONNXRUNTIME,
            "Det.ocr_version": OCRVersion.PPOCRV5,
            "Det.lang_type": LangDet.MULTI,
            "Det.model_type": ModelType.MOBILE,
            "Det.model_path": str(detector),
            "Det.limit_type": "max",
            "Det.limit_side_len": 1280,
            "Rec.engine_type": EngineType.ONNXRUNTIME,
            "Rec.ocr_version": OCRVersion.PPOCRV5,
            "Rec.lang_type": LangRec.CH,
            "Rec.model_type": ModelType.MOBILE,
            "Rec.model_path": str(recognition),
            "EngineConfig.onnxruntime.intra_op_num_threads": 2,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        })

    def warm(self):
        image = np.full((76, 270, 3), 255, dtype=np.uint8)
        cv2.putText(image, "Local OCR", (12, 47), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 2)
        self.recognize(image)

    def recognize(self, image: np.ndarray) -> str:
        result = self.engine(image)
        if not result.txts:
            return ""
        # RapidOCR returns regions in reading order. Text regions on the same
        # line may be separate boxes, including on small GitHub fonts.
        lines = []
        previous = None
        for box, text in zip(result.boxes, result.txts, strict=True):
            top, bottom = float(box[:, 1].min()), float(box[:, 1].max())
            left = float(box[:, 0].min())
            center = (top + bottom) / 2
            height = max(bottom - top, 1.0)
            if previous:
                prev_left, prev_center, prev_height = previous
                same_line = left > prev_left and abs(center - prev_center) < 0.5 * max(height, prev_height)
            else:
                same_line = False
            if same_line:
                lines[-1].append(text)
            else:
                lines.append([text])
            previous = left, center, height
        return "\n".join(" ".join(line) for line in lines)


class OcrWorker(QObject):
    finished = Signal(int, str, str)

    def __init__(self, directory: Path):
        super().__init__()
        self.directory = directory
        self.recognizer = None

    @Slot()
    def initialize(self):
        if self.recognizer is None:
            try:
                recognizer = Recognizer(self.directory)
                recognizer.warm()
                self.recognizer = recognizer
            except Exception:
                # A selection will try again and display a useful error.
                pass

    @Slot(int, object)
    def run(self, request_id, image):
        try:
            if self.recognizer is None:
                self.recognizer = Recognizer(self.directory)
            text = self.recognizer.recognize(image)
        except FileNotFoundError as error:
            self.finished.emit(request_id, "", str(error))
        except Exception:
            self.recognizer = None
            self.finished.emit(request_id, "", "本地 OCR 处理失败，请重试。")
        else:
            self.finished.emit(request_id, text, "")
