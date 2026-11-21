from ctypes import POINTER, byref, wintypes
import os
from pathlib import Path
import subprocess

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QCursor

from conftest import sse_delta
from test_desktop import USER32, invoke_hotkey, open_source, until, wait
from transformagent.hotkey import DEFAULT_HOTKEY, Shortcut
from transformagent.settings import Settings
from transformagent.windows import capture_at_cursor, flush_desktop

pytestmark = pytest.mark.desktop

USER32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
USER32.FindWindowW.restype = wintypes.HWND
USER32.IsWindowVisible.argtypes = (wintypes.HWND,)
USER32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
USER32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, POINTER(wintypes.DWORD))


def test_distributed_executable_capture_and_ocr(app, http_endpoint, monkeypatch):
    executable_arg = os.getenv("TRANSFORMAGENT_PACKAGE_TEST")
    if not executable_arg:
        pytest.skip("Set TRANSFORMAGENT_PACKAGE_TEST to the packaged executable")
    executable = Path(executable_arg).resolve(strict=True)
    try:
        shortcut = Shortcut.parse(Settings().hotkey())
    except ValueError:
        shortcut = Shortcut.parse(DEFAULT_HOTKEY)
    if shortcut != Shortcut.parse(DEFAULT_HOTKEY):
        pytest.skip("The package test only sends the default shortcut")

    def respond(handler):
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.end_headers()
        handler.wfile.write(sse_delta("这个 pull request 修复了 race condition。") + b"data: [DONE]\n\n")

    endpoint, calls = http_endpoint(respond)
    monkeypatch.setenv("TRANSFORMAGENT_API_URL", endpoint)
    monkeypatch.setenv("TRANSFORMAGENT_API_KEY", "synthetic-key")
    monkeypatch.setenv("TRANSFORMAGENT_MODEL", "qwen-mt-flash")
    english = "This pull request fixes a race condition."
    source = open_source(app, [english])
    previous_cursor = QCursor.pos()
    # Use an absolute path and a different working directory to prove that
    # the executable loads its bundled assets rather than checkout files.
    process = subprocess.Popen([executable], cwd=executable.parent)
    hwnd = None
    try:
        def package_window(title):
            window = USER32.FindWindowW(None, title)
            pid = wintypes.DWORD()
            if window:
                USER32.GetWindowThreadProcessId(window, byref(pid))
                if pid.value == process.pid and USER32.IsWindowVisible(window):
                    return window
            return None

        source.raise_()
        source.activateWindow()
        wait(120)
        flush_desktop()
        QCursor.setPos(source.mapToGlobal(QPoint(9, 7)))

        def fixture_pixel():
            screen, frame = capture_at_cursor()
            bounds = screen.geometry()
            marker = source.mapToGlobal(QPoint(5, 5)) - bounds.topLeft()
            sample = frame.crop(QRect(marker, marker), bounds.width(), bounds.height())
            return tuple(int(channel) for channel in sample[0, 0])

        def packaged_overlay_opened():
            if package_window("TransformAgent 框选") or process.poll() is not None:
                return True
            if fixture_pixel() != (111, 162, 63):
                source.raise_()
                source.activateWindow()
                return False
            invoke_hotkey()
            wait(180)
            return package_window("TransformAgent 框选") is not None

        until(packaged_overlay_opened, timeout=15)
        assert process.poll() is None
        # The selection overlay dims the exact fixture marker. Avoid dragging
        # over real desktop content if another window interrupted the setup.
        assert all(abs(actual - expected) <= 2 for actual, expected in zip(fixture_pixel(), (73, 96, 39)))
        QCursor.setPos(source.mapToGlobal(QPoint(9, 7)))
        USER32.mouse_event(0x0002, 0, 0, 0, 0)
        try:
            wait(35)
            QCursor.setPos(source.mapToGlobal(QPoint(770, 111)))
            wait(60)
        finally:
            USER32.mouse_event(0x0004, 0, 0, 0, 0)
        until(lambda: bool(calls) or process.poll() is not None, 12)
        assert process.poll() is None
        assert calls[0][2]["messages"][0]["content"] == english, "Unexpected OCR text in the test request"
        hwnd = package_window("TransformAgent 翻译")
        assert hwnd
    finally:
        source.close()
        QCursor.setPos(previous_cursor)
        if hwnd:
            USER32.PostMessageW(hwnd, 0x0010, 0, 0)
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
