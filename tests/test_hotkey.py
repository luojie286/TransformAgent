import pytest

from transformagent.hotkey import ChordState, Shortcut


@pytest.mark.parametrize(("text", "expected"), [
    ("a+s+alt", "Alt+A+S"),
    (" shift +cTrl+k ", "Ctrl+Shift+K"),
    ("win+5", "Win+5"),
    ("alt+f5", "Alt+F5"),
    ("ctrl+space", "Ctrl+Space"),
])
def test_shortcut_input_is_canonicalized(text, expected):
    assert str(Shortcut.parse(text)) == expected


@pytest.mark.parametrize("text", ["", "A+S", "Alt", "Alt++S", "Ctrl+Ctrl+A", "Alt+A+A", "Alt+F13"])
def test_ambiguous_or_unsupported_shortcuts_are_rejected(text):
    with pytest.raises(ValueError):
        Shortcut.parse(text)


def test_completed_chord_triggers_once_and_swallows_the_completing_key():
    chord = ChordState(Shortcut.parse("Alt+A+S"))

    assert chord.process(0xA4, True) == (False, False)
    assert chord.process(ord("A"), True) == (False, False)
    assert chord.process(ord("S"), True) == (True, True)
    assert chord.process(ord("S"), True) == (False, True)
    assert chord.process(ord("S"), False) == (False, True)
    assert chord.process(ord("S"), True) == (True, True)
    assert chord.process(ord("S"), False) == (False, True)
    assert chord.process(ord("A"), False) == (False, False)
    assert chord.process(0xA4, False) == (False, False)


def test_incomplete_or_extended_shortcut_passes_through():
    chord = ChordState(Shortcut.parse("Alt+A+S"))

    assert chord.process(0xA4, True) == (False, False)
    assert chord.process(ord("S"), True) == (False, False)
    assert chord.process(0x11, True) == (False, False)
    assert chord.process(ord("A"), True) == (False, False)
    assert chord.process(0x11, False) == (False, False)
