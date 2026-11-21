"""The selection overlay, bilingual result, and endpoint settings."""

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QPixmap, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)
from keyring.errors import KeyringError


FLOAT_FLAGS = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint


class SelectionOverlay(QWidget):
    selected = Signal(QRect)
    cancelled = Signal()

    def __init__(self, screen, frame):
        super().__init__(None, FLOAT_FLAGS)
        self.setWindowTitle("TransformAgent 框选")
        self.screen_snapshot = frame
        self.pixmap = QPixmap.fromImage(frame.image())
        self.start = None
        self.end = None
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.winId()
        self.windowHandle().setScreen(screen)
        self.setGeometry(screen.geometry())

    def open(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def selection(self) -> QRect:
        if self.start is None or self.end is None:
            return QRect()
        return QRect(self.start, self.end).normalized().intersected(self.rect())

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.drawPixmap(self.rect(), self.pixmap)
        painter.fillRect(self.rect(), QColor(9, 15, 26, 115))
        rect = self.selection()
        if not rect.isEmpty():
            painter.save()
            painter.setClipRect(rect)
            painter.drawPixmap(self.rect(), self.pixmap)
            painter.restore()
            painter.setPen(QPen(QColor("#77b8ff"), 2))
            painter.drawRect(rect.adjusted(0, 0, -1, -1))
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.cancelled.emit()
        elif event.button() == Qt.MouseButton.LeftButton:
            self.start = self.end = event.position().toPoint()
            self.update()

    def mouseMoveEvent(self, event):
        if self.start is not None:
            self.end = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.start is not None:
            self.end = event.position().toPoint()
            rect = self.selection()
            if rect.width() >= 8 and rect.height() >= 8:
                self.selected.emit(rect)
            else:
                self.start = self.end = None
                self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
        else:
            super().keyPressEvent(event)


def floating_geometry(anchor: QRect, work_area: QRect, size: QSize) -> QRect:
    gap = 8
    width = min(work_area.width(), size.width())
    height = min(work_area.height(), size.height())
    positions = (
        QPoint(anchor.left(), anchor.bottom() + gap + 1),
        QPoint(anchor.right() + gap + 1, anchor.top()),
        QPoint(anchor.left(), anchor.top() - height - gap),
        QPoint(anchor.left() - width - gap, anchor.top()),
    )
    for index, point in enumerate(positions):
        if index in (0, 2):
            point.setX(max(work_area.left(), min(point.x(), work_area.right() - width + 1)))
        else:
            point.setY(max(work_area.top(), min(point.y(), work_area.bottom() - height + 1)))
        candidate = QRect(point, QSize(width, height))
        if work_area.contains(candidate):
            return candidate
    x = max(work_area.left(), min(anchor.left(), work_area.right() - width + 1))
    y = max(work_area.top(), min(anchor.bottom() + gap + 1, work_area.bottom() - height + 1))
    return QRect(x, y, width, height)


class TranslationWindow(QWidget):
    dismissed = Signal()
    retry = Signal()
    configure = Signal()

    def __init__(self):
        super().__init__(None, FLOAT_FLAGS)
        self.anchor = QRect()
        self.selection_size = QSize()
        self.setObjectName("translationWindow")
        self.setWindowTitle("TransformAgent 翻译")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # 牧濑红莉栖红主题：酒红主色取自红莉栖的发色与开衫，米白底色呼应她的白大褂。
        self.setStyleSheet("""
            QWidget#translationWindow { background: #fbf4f1; border: 2px solid #8e2f41; }
            QLabel { color: #4a222b; }
            QLabel#section { color: #8e2f41; font-weight: bold; }
            QPlainTextEdit { background: #ffffff; color: #3d2126; border: 1px solid #e0c2bf;
                             border-radius: 5px; padding: 5px; }
            QPushButton { background: #8e2f41; color: #fff7f4; border: 1px solid #6e2231;
                          border-radius: 4px; padding: 4px 12px; }
            QPushButton:hover { background: #a13a4c; }
            QPushButton:pressed { background: #6e2231; }
        """)
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.status = QLabel()
        header.addWidget(self.status, 1)
        self.settings_button = QPushButton("设置")
        self.settings_button.clicked.connect(self.configure)
        self.retry_button = QPushButton("重试")
        self.retry_button.clicked.connect(self.retry)
        close = QPushButton("关闭")
        close.clicked.connect(self.dismissed)
        for button in (self.settings_button, self.retry_button, close):
            header.addWidget(button)
        layout.addLayout(header)

        panes = QHBoxLayout()
        self.original = self._pane(panes, "原文", "originalText")
        self.translated = self._pane(panes, "中文", "translatedText")
        layout.addLayout(panes, 1)
        self.escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.escape.activated.connect(self.dismissed)

    def _pane(self, panes, title, name):
        column = QVBoxLayout()
        label = QLabel(title)
        label.setObjectName("section")
        column.addWidget(label)
        text = QPlainTextEdit(readOnly=True)
        text.setObjectName(name)
        column.addWidget(text)
        panes.addLayout(column, 1)
        return text

    def open(self, screen, anchor, size):
        self.original.clear()
        self.translated.clear()
        self.hide()
        self.anchor = anchor
        self.selection_size = size
        self.set_status("正在识别…")
        self.winId()
        self.windowHandle().setScreen(screen)
        self.fit_to_screen(screen)
        self.show()
        self.raise_()

    def fit_to_screen(self, screen):
        self.layout().activate()
        # The side-by-side panes need room even for a one-line capture.
        # Include the current header's minimum before choosing an anchor side.
        size = self.selection_size.expandedTo(QSize(420, 180)).expandedTo(self.minimumSizeHint())
        self.setGeometry(floating_geometry(self.anchor, screen.availableGeometry(), size))

    def set_status(self, text, *, retry=False, configure=False):
        self.status.setText(text)
        self.retry_button.setVisible(retry)
        self.settings_button.setVisible(configure)
        if self.isVisible():
            self.fit_to_screen(self.screen())

    def append_translation(self, fragment):
        cursor = self.translated.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(fragment)

    def closeEvent(self, event):
        self.dismissed.emit()
        super().closeEvent(event)


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("TransformAgent 设置")
        self.setMinimumWidth(510)
        form = QFormLayout(self)
        self.address = QLineEdit(settings.endpoint())
        self.model = QLineEdit(settings.model())
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("默认百炼端点可读取 QWEN_API_KEY")
        form.addRow("接口地址", self.address)
        form.addRow("模型", self.model)
        form.addRow("API Key", self.key)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def accept(self):
        try:
            self.settings.save(self.address.text(), self.model.text(), self.key.text())
        except ValueError as error:
            QMessageBox.warning(self, "设置未保存", str(error))
        except KeyringError:
            QMessageBox.warning(self, "设置未保存", "无法写入 Windows 凭据管理器。")
        else:
            self.key.clear()
            super().accept()
