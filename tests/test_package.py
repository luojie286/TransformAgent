from ctypes import wintypes
import os
from pathlib import Path
import subprocess

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QCursor

from conftest import sse_delta
from test_desktop import USER32, invoke_hotkey, open_source, until, wait
from transformagent.windows import HOTKEY_ID, MOD_NOREPEAT

pytestmark = pytest.mark.desktop

USER32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
USER32.FindWindowW.restype = wintypes.HWND
USER32.IsWindowVisible.argtypes = (wintypes.HWND,)
USER32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


def test_distributed_executable_capture_and_ocr(app, http_endpoint, monkeypatch):
    executable_arg = os.getenv("TRANSFORMAGENT_PACKAGE_TEST")
    if not executable_arg:
        pytest.skip("Set TRANSFORMAGENT_PACKAGE_TEST to the packaged executable")
    executable = Path(executable_arg).resolve(strict=True)
    from transformagent.windows import USER32 as hotkey_api

    def alt_s_available():
        if not hotkey_api.RegisterHotKey(None, HOTKEY_ID, 0x0001 | MOD_NOREPEAT, ord("S")):
            return False
        hotkey_api.UnregisterHotKey(None, HOTKEY_ID)
        return True

    assert alt_s_available(), "Alt+S is already in use"

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
        def package_registered_hotkey():
            if process.poll() is not None:
                return True
            return not alt_s_available()

        until(package_registered_hotkey, timeout=15)
        assert process.poll() is None
        source.raise_()
        source.activateWindow()
        wait(120)
        QCursor.setPos(source.mapToGlobal(QPoint(9, 7)))
        invoke_hotkey()
        until(lambda: USER32.IsWindowVisible(USER32.FindWindowW(None, "TransformAgent 框选")), timeout=8)
        USER32.mouse_event(0x0002, 0, 0, 0, 0)
        try:
            QCursor.setPos(source.mapToGlobal(QPoint(770, 111)))
            wait(60)
        finally:
            USER32.mouse_event(0x0004, 0, 0, 0, 0)
        until(lambda: bool(calls) or process.poll() is not None, 12)
        assert process.poll() is None
        assert calls[0][2]["messages"][0]["content"] == english
        hwnd = USER32.FindWindowW(None, "TransformAgent 翻译")
        assert hwnd and USER32.IsWindowVisible(hwnd)
    finally:
        source.close()
        QCursor.setPos(previous_cursor)
        if hwnd:
            USER32.PostMessageW(hwnd, 0x0010, 0, 0)
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
