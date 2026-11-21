import ctypes
import os
from time import perf_counter

import pytest
from PySide6.QtCore import QEventLoop, QPoint, QRect, QSettings, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QInputDialog, QWidget

from conftest import sse_delta
from transformagent.app import Workflow
from transformagent.hotkey import Shortcut, USER32 as HOTKEY_API
from transformagent.settings import Settings, qwen_environment_key
from transformagent.windows import flush_desktop


pytestmark = pytest.mark.desktop
USER32 = ctypes.WinDLL("user32")


def until(predicate, timeout=10):
    deadline = perf_counter() + timeout
    while not predicate() and perf_counter() < deadline:
        wait(15)
    assert predicate()


def wait(milliseconds):
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


class SourceWindow(QWidget):
    def __init__(self, lines, font):
        super().__init__(None, Qt.WindowType.WindowStaysOnTopHint)
        self.lines = lines
        self.font = QFont(font)
        self.font.setPixelSize(23)
        self.setWindowTitle("TransformAgent synthetic test source")
        self.resize(790, 145)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("white"))
        painter.fillRect(3, 3, 5, 4, QColor("#3fa26f"))
        painter.setPen(QColor("#141923"))
        painter.setFont(self.font)
        for row, line in enumerate(self.lines):
            painter.drawText(22, 37 + row * 36, line)
        painter.end()


def invoke_hotkey(keys=(0x12, ord("A"), ord("S"))):
    for key in keys:
        USER32.keybd_event(key, 0, 0, 0)
    for key in reversed(keys):
        USER32.keybd_event(key, 0, 2, 0)


def select_text(workflow, source):
    start = source.mapToGlobal(QPoint(9, 7))
    end = source.mapToGlobal(QPoint(770, 111))
    for _ in range(3):
        source.raise_()
        source.activateWindow()
        wait(150)
        flush_desktop()
        QCursor.setPos(start)
        invoke_hotkey()
        until(lambda: workflow.overlay is not None and workflow.overlay.isVisible())
        overlay = workflow.overlay
        assert overlay.geometry().contains(start)
        # DWM can still show a preceding window on the first capture. Check
        # the fixture's marker before allowing live tests to send any text.
        marker = overlay.mapFromGlobal(source.mapToGlobal(QPoint(5, 5)))
        sample = overlay.screen_snapshot.crop(QRect(marker, marker), overlay.width(), overlay.height())
        if tuple(sample[0, 0]) == (0x6F, 0xA2, 0x3F):
            break
        workflow.close_overlay()
    else:
        pytest.fail("Capture does not show the test fixture")
    QCursor.setPos(start)
    USER32.mouse_event(0x0002, 0, 0, 0, 0)
    wait(35)
    QCursor.setPos(end)
    wait(35)
    before_mouse_up = perf_counter()
    USER32.mouse_event(0x0004, 0, 0, 0, 0)
    until(lambda: workflow.window.isVisible() and workflow.overlay is None)
    return before_mouse_up


@pytest.fixture(scope="module")
def desktop(app, tmp_path_factory):
    if os.getenv("QT_QPA_PLATFORM") == "offscreen":
        pytest.skip("An interactive Windows desktop is required")
    store = QSettings(str(tmp_path_factory.mktemp("desktop-settings") / "settings.ini"), QSettings.Format.IniFormat)
    workflow = Workflow(app, settings=Settings(store))
    previous_cursor = QCursor.pos()
    try:
        assert workflow.hotkey.active, "The default OS hotkey is in use"
        yield workflow
    finally:
        workflow.close()
        QCursor.setPos(previous_cursor)
        workflow.deleteLater()
        wait(1)


def open_source(app, lines, font="Segoe UI"):
    source = SourceWindow(lines, font)
    available = app.primaryScreen().availableGeometry()
    source.move(available.left() + 45, available.top() + 85)
    source.show()
    return source


def test_shortcut_setting_is_saved_and_windows_conflicts_keep_it(app, desktop):
    original = desktop.settings.hotkey()

    def set_dialog_text():
        dialog = app.activeModalWidget()
        if isinstance(dialog, QInputDialog):
            dialog.setTextValue("shift+ctrl+f9")
            dialog.accept()

    QTimer.singleShot(0, set_dialog_text)
    desktop.hotkey_action.trigger()
    try:
        saved = Settings(QSettings(desktop.settings.store.fileName(), QSettings.Format.IniFormat))
        assert saved.hotkey() == "Ctrl+Shift+F9"
        assert "Ctrl+Shift+F9" in desktop.capture_action.text()
        invoke_hotkey((0x11, 0x10, 0x78))  # Ctrl+Shift+F9
        until(lambda: desktop.overlay is not None)
        desktop.close_overlay()

        occupied = Shortcut.parse("Ctrl+Alt+F10")
        # Reserve a real Windows shortcut, independently of the app binding.
        identifier = 0x5200
        reserved = HOTKEY_API.RegisterHotKey(None, identifier, 0x0003, 0x79)
        try:
            assert not desktop.hotkey.register(occupied)
            assert str(desktop.hotkey.shortcut) == "Ctrl+Shift+F9"
            assert saved.hotkey() == "Ctrl+Shift+F9"
            invoke_hotkey((0x11, 0x10, 0x78))
            until(lambda: desktop.overlay is not None)
        finally:
            if reserved:
                HOTKEY_API.UnregisterHotKey(None, identifier)
    finally:
        desktop.close_overlay()
        assert desktop.hotkey.register(Shortcut.parse(original))
        desktop.settings.save_hotkey(original)
        desktop.update_hotkey_label()

def test_global_hotkey_drag_real_ocr_popup_and_retry(app, desktop, http_endpoint, monkeypatch):
    attempts = []

    def respond(handler):
        attempts.append(True)
        if len(attempts) == 1:
            handler.send_error(429)
            return
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.end_headers()
        handler.wfile.write(sse_delta("这个 pull request 修复了 React 中的 race condition。") + b"data: [DONE]\n\n")

    endpoint, calls = http_endpoint(respond)
    monkeypatch.setenv("TRANSFORMAGENT_API_URL", endpoint)
    monkeypatch.setenv("TRANSFORMAGENT_API_KEY", "synthetic-key")
    monkeypatch.setenv("TRANSFORMAGENT_MODEL", "qwen-mt-flash")
    english = "This pull request fixes a race condition in React."
    source = open_source(app, [english])
    try:
        select_text(desktop, source)
        until(lambda: desktop.window.status.text() != "正在识别…")
        until(lambda: desktop.window.status.text() != "正在翻译…")
        assert "限流" in desktop.window.status.text()
        assert desktop.window.original.toPlainText() == english
        assert desktop.window.geometry().top() > source.mapToGlobal(QPoint(0, 111)).y()
        assert app.primaryScreen().availableGeometry().contains(desktop.window.geometry())
        QTest.mouseClick(desktop.window.retry_button, Qt.MouseButton.LeftButton)
        until(lambda: desktop.window.status.text() == "翻译完成")
        assert desktop.window.translated.toPlainText() == "这个 pull request 修复了 React 中的 race condition。"
        assert [call[2]["messages"][0]["content"] for call in calls] == [english, english]
    finally:
        source.close()


@pytest.mark.parametrize(("language", "lines", "font"), [
    ("en", ["This pull request fixes a race condition in React.", "Please run the tests again."], "Segoe UI"),
    ("ja", ["この pull request は React の race condition を修正します。", "テストを再実行してください。"], "Yu Gothic"),
])
def test_live_qwen_from_desktop(app, desktop, language, lines, font):
    if os.getenv("TRANSFORMAGENT_LIVE_TEST") != "1":
        pytest.skip("Enable the real Qwen API tests explicitly")
    assert qwen_environment_key(), "QWEN_API_KEY is not available"
    source = open_source(app, lines, font)
    try:
        start = select_text(desktop, source)
        until(lambda: "。" in desktop.window.translated.toPlainText() or desktop.window.retry_button.isVisible(), 25)
        first_ms = round((perf_counter() - start) * 1000)
        until(lambda: desktop.window.status.text() == "翻译完成" or desktop.window.retry_button.isVisible(), 25)
        elapsed = round((perf_counter() - start) * 1000)
        assert desktop.window.status.text() == "翻译完成"
        original = desktop.window.original.toPlainText()
        translation = desktop.window.translated.toPlainText()
        for term in ("pull request", "race condition", "React"):
            assert term in original and term in translation
        assert "修复" in translation and "测试" in translation
        print(f"{language}: first sentence {first_ms} ms, complete {elapsed} ms")
    finally:
        source.close()
