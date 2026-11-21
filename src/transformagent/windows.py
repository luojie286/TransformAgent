"""Windows in-memory monitor capture."""

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from math import ceil, floor

import numpy as np
from mss import MSS
from PySide6.QtCore import QRect
from PySide6.QtGui import QCursor, QGuiApplication, QImage


USER32 = ctypes.WinDLL("user32", use_last_error=True)
USER32.GetPhysicalCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)


@dataclass
class ScreenFrame:
    width: int
    height: int
    pixels: bytes

    def image(self) -> QImage:
        # MSS uses BGRA. Qt's RGB32 has the same memory layout on Windows.
        return QImage(self.pixels, self.width, self.height, self.width * 4, QImage.Format.Format_RGB32)

    def crop(self, rect: QRect, logical_width: int, logical_height: int) -> np.ndarray:
        sx = self.width / logical_width
        sy = self.height / logical_height
        left = max(0, floor(rect.x() * sx))
        top = max(0, floor(rect.y() * sy))
        right = min(self.width, ceil((rect.x() + rect.width()) * sx))
        bottom = min(self.height, ceil((rect.y() + rect.height()) * sy))
        image = np.frombuffer(self.pixels, dtype=np.uint8).reshape(self.height, self.width, 4)
        return np.ascontiguousarray(image[top:bottom, left:right, :3])


def capture_at_cursor():
    screen = QGuiApplication.screenAt(QCursor.pos())
    cursor = wintypes.POINT()
    if screen is None or not USER32.GetPhysicalCursorPos(ctypes.byref(cursor)):
        raise RuntimeError("无法确定当前显示器。")
    with MSS() as mss:
        for monitor in mss.monitors[1:]:
            left, top = monitor["left"], monitor["top"]
            if left <= cursor.x < left + monitor["width"] and top <= cursor.y < top + monitor["height"]:
                capture = mss.grab(monitor)
                return screen, ScreenFrame(capture.width, capture.height, bytes(capture.bgra))
    raise RuntimeError("无法截取当前显示器。")


def flush_desktop():
    # Ensure the previous floating window is no longer painted before capturing.
    ctypes.WinDLL("dwmapi").DwmFlush()
