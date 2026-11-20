import numpy as np
from PIL import Image, ImageDraw, ImageFont
import pytest
from PySide6.QtCore import QRect, QSize

from transformagent.ocr import Recognizer, model_directory
from transformagent.ui import floating_geometry
from transformagent.windows import ScreenFrame


@pytest.fixture(scope="module")
def recognizer():
    engine = Recognizer(model_directory())
    engine.warm()
    return engine


@pytest.mark.parametrize(("font", "background", "foreground", "lines", "expected"), [
    ("segoeui.ttf", "#0d1117", "#e6edf3",
     ["This pull request fixes a race condition in React.", "Run GitHub Actions to verify the fix."],
     ["This pull request fixes a race condition in React.", "Run GitHub Actions to verify the fix."]),
    ("YuGothM.ttc", "white", "black",
     ["この pull request は React の race condition を修正します。", "テストを再実行してください。"],
     ["このpullrequestはReactのraceconditionを修正します。", "テストを再実行してください。"]),
])
def test_real_ocr_on_english_and_japanese_images(recognizer, font, background, foreground, lines, expected):
    image = Image.new("RGB", (840, 142), background)
    draw = ImageDraw.Draw(image)
    for row, line in enumerate(lines):
        face = ImageFont.truetype("C:/Windows/Fonts/" + font, 22)
        draw.text((18, 14 + row * 34), line, fill=foreground, font=face)
    pixels = np.ascontiguousarray(np.asarray(image)[:, :, ::-1])
    actual = recognizer.recognize(pixels)
    if font.startswith("Yu"):
        assert actual.replace(" ", "").splitlines() == expected
    else:
        assert actual.splitlines() == expected


def test_blank_image_does_not_invent_text(recognizer):
    assert recognizer.recognize(np.full((100, 300, 3), 255, dtype=np.uint8)) == ""


def test_mixed_dpi_crop_maps_logical_rectangle_to_physical_pixels():
    pixels = np.arange(12 * 8 * 4, dtype=np.uint8).reshape(8, 12, 4)
    frame = ScreenFrame(12, 8, pixels.tobytes())
    selection = frame.crop(QRect(1, 1, 2, 2), 6, 4)
    np.testing.assert_array_equal(selection, pixels[2:6, 2:6, :3])


def test_window_chooses_below_when_it_fits_and_stays_in_work_area():
    screen = QRect(-1920, 0, 1920, 1040)
    size = QSize(600, 238)
    selection = QRect(-120, 100, 100, 80)
    below = floating_geometry(selection, screen, size)
    assert below.top() > selection.bottom()
    assert screen.contains(below)
    near_bottom = QRect(-250, 950, 240, 80)
    fallback = floating_geometry(near_bottom, screen, size)
    assert screen.contains(fallback)
    assert not fallback.intersects(near_bottom)
