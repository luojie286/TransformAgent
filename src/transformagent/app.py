"""Application entry point and selection-to-translation workflow."""

import logging
import re
import sys
from time import perf_counter

from keyring.errors import KeyringError
from mss.exception import ScreenShotError
from PySide6.QtCore import QObject, QPoint, QThread, Signal
from PySide6.QtWidgets import QApplication, QInputDialog, QMenu, QMessageBox, QStyle, QSystemTrayIcon

from transformagent.hotkey import DEFAULT_HOTKEY, GlobalHotkey, Shortcut
from transformagent.ocr import OcrWorker, model_directory
from transformagent.settings import Settings
from transformagent.translation import TranslationClient
from transformagent.ui import SelectionOverlay, SettingsDialog, TranslationWindow
from transformagent.windows import capture_at_cursor, flush_desktop


LOGGER = logging.getLogger(__name__)
SENTENCE_END = re.compile(r"[。！？!?]|\.(?=\s)")


class Workflow(QObject):
    recognized_job = Signal(int, object)

    def __init__(self, application, *, settings=None, client=None, ocr_directory=None):
        super().__init__()
        self.application = application
        self.settings = settings if settings is not None else Settings()
        self.client = client if client is not None else TranslationClient(self)
        self.window = TranslationWindow()
        self.window.dismissed.connect(self.dismiss)
        self.window.retry.connect(self.translate)
        self.window.configure.connect(self.open_settings)
        self.overlay = None
        self.request = None
        self.request_id = 0
        self.source = ""
        self.ocr_running = False
        self.pending = None
        self.started = 0.0
        self.first_sentence = False
        self.received = ""
        self.worker_thread = QThread(self)
        self.worker = OcrWorker(ocr_directory or model_directory())
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.initialize)
        self.worker_thread.finished.connect(self.worker.deleteLater)
        self.recognized_job.connect(self.worker.run)
        self.worker.finished.connect(self.on_ocr_finished)
        self.worker_thread.start()
        self.hotkey = GlobalHotkey(application, self.select)
        self.tray = QSystemTrayIcon(application.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView), self)
        try:
            shortcut = Shortcut.parse(self.settings.hotkey())
        except ValueError:
            shortcut = Shortcut.parse(DEFAULT_HOTKEY)
        self.hotkey.register(shortcut)
        menu = QMenu()
        self.capture_action = menu.addAction("框选翻译", self.select)
        self.hotkey_action = menu.addAction("快捷键设置", self.open_hotkey_settings)
        menu.addAction("设置", self.open_settings)
        menu.addSeparator()
        menu.addAction("退出", application.quit)
        self.menu = menu
        self.tray.setContextMenu(menu)
        self.update_hotkey_label()
        self.tray.show()
        if not self.hotkey.active:
            self.notify("无法启用全局快捷键。可在托盘里修改快捷键或直接开始框选。")

    def notify(self, message):
        self.tray.showMessage("TransformAgent", message, QSystemTrayIcon.MessageIcon.Warning)

    def update_hotkey_label(self):
        shortcut = self.hotkey.shortcut
        self.tray.setToolTip(f"TransformAgent · {shortcut}" if shortcut else "TransformAgent")
        self.capture_action.setText(f"框选翻译（{shortcut}）" if shortcut else "框选翻译")

    def open_hotkey_settings(self):
        parent = self.window if self.window.isVisible() else None
        text = self.settings.hotkey()
        while True:
            text, accepted = QInputDialog.getText(
                parent, "框选快捷键", "输入组合，例如 Ctrl+Shift+S 或 Alt+A+S。", text=text
            )
            if not accepted:
                return
            try:
                shortcut = Shortcut.parse(text)
            except ValueError as error:
                QMessageBox.warning(parent, "快捷键未更改", str(error))
                continue
            if not self.hotkey.register(shortcut):
                QMessageBox.warning(parent, "快捷键未更改", "无法启用这个组合键，可能已被占用。")
                continue
            self.update_hotkey_label()
            try:
                self.settings.save_hotkey(str(shortcut))
            except OSError as error:
                QMessageBox.warning(parent, "快捷键保存失败", str(error))
            return

    def select(self):
        if self.overlay is not None:
            self.close_overlay()
            return
        previous_window_visible = self.window.isVisible()
        self.dismiss()
        if previous_window_visible:
            flush_desktop()
        try:
            screen, frame = capture_at_cursor()
        except (RuntimeError, OSError, ScreenShotError):
            self.notify("截取屏幕失败，请重试。")
            return
        self.overlay = SelectionOverlay(screen, frame)
        self.overlay.selected.connect(lambda rect: self.on_selected(screen, rect))
        self.overlay.cancelled.connect(self.close_overlay)
        self.overlay.open()

    def close_overlay(self):
        if self.overlay is not None:
            self.overlay.hide()
            self.overlay.deleteLater()
            self.overlay = None

    def on_selected(self, screen, rect):
        self.started = perf_counter()
        overlay = self.overlay
        anchor = rect.translated(overlay.mapToGlobal(QPoint()))
        image = overlay.screen_snapshot.crop(rect, overlay.width(), overlay.height())
        self.close_overlay()
        self.window.open(screen, anchor)
        if self.ocr_running:
            # Keep just the latest crop while a previous native inference exits.
            self.pending = self.request_id, image
        else:
            self.run_ocr(self.request_id, image)

    def run_ocr(self, request_id, image):
        self.ocr_running = True
        self.recognized_job.emit(request_id, image)

    def on_ocr_finished(self, request_id, source, error):
        self.ocr_running = False
        if self.pending is not None:
            pending, self.pending = self.pending, None
            self.run_ocr(*pending)
        if request_id != self.request_id:
            return
        self.log_elapsed("ocr")
        if error:
            self.window.set_status(error)
        elif not source.strip():
            self.window.set_status("未识别到文字，请重新框选。")
        else:
            self.source = source
            self.window.original.setPlainText(source)
            self.translate()

    def translate(self):
        if not self.source:
            return
        try:
            config = self.settings.config()
        except (ValueError, KeyringError):
            self.window.set_status("无法读取 API 设置，请检查配置。", configure=True)
            return
        if not config.key:
            self.window.set_status("请配置 API Key 后重试。", retry=True, configure=True)
            return
        self.cancel_translation()
        self.received = ""
        self.first_sentence = False
        self.window.translated.clear()
        self.window.set_status("正在翻译…")
        request_id = self.request_id
        self.request = self.client.start(config, self.source)
        self.request.delta.connect(lambda text: self.on_delta(request_id, text))
        self.request.succeeded.connect(lambda: self.on_translated(request_id))
        self.request.failed.connect(lambda message: self.on_error(request_id, message))

    def on_delta(self, request_id, text):
        if request_id == self.request_id:
            self.window.append_translation(text)
            self.received += text
            if not self.first_sentence and SENTENCE_END.search(self.received):
                self.first_sentence = True
                self.log_elapsed("first_sentence")

    def on_translated(self, request_id):
        if request_id == self.request_id:
            self.request = None
            if not self.first_sentence:
                self.log_elapsed("first_sentence")
            self.log_elapsed("complete")
            self.window.set_status("翻译完成")

    def on_error(self, request_id, message):
        if request_id == self.request_id:
            self.request = None
            self.window.set_status(message, retry=True, configure=True)

    def log_elapsed(self, phase):
        LOGGER.info("%s elapsed_ms=%d", phase, (perf_counter() - self.started) * 1000)

    def cancel_translation(self):
        if self.request is not None:
            self.request.cancel()
            self.request = None

    def dismiss(self):
        self.request_id += 1
        self.source = ""
        self.pending = None
        self.cancel_translation()
        self.window.hide()

    def open_settings(self):
        dialog = SettingsDialog(self.settings, self.window if self.window.isVisible() else None)
        if dialog.exec() and self.source:
            self.translate()

    def close(self):
        self.close_overlay()
        self.dismiss()
        self.hotkey.close()
        self.tray.hide()
        self.worker_thread.quit()
        self.worker_thread.wait()


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    application = QApplication(sys.argv)
    application.setApplicationName("TransformAgent")
    application.setQuitOnLastWindowClosed(False)
    workflow = Workflow(application)
    try:
        return application.exec()
    finally:
        workflow.close()


if __name__ == "__main__":
    raise SystemExit(main())
