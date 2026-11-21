"""Parse shortcuts and register their Windows keyboard bindings."""

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

from PySide6.QtCore import QAbstractNativeEventFilter, QTimer


DEFAULT_HOTKEY = "Ctrl+Alt+Shift+Y"
MODIFIERS = {"Ctrl": 0x0002, "Alt": 0x0001, "Shift": 0x0004, "Win": 0x0008}
MODIFIER_KEYS = {
    0x11: MODIFIERS["Ctrl"], 0xA2: MODIFIERS["Ctrl"], 0xA3: MODIFIERS["Ctrl"],
    0x12: MODIFIERS["Alt"], 0xA4: MODIFIERS["Alt"], 0xA5: MODIFIERS["Alt"],
    0x10: MODIFIERS["Shift"], 0xA0: MODIFIERS["Shift"], 0xA1: MODIFIERS["Shift"],
    0x5B: MODIFIERS["Win"], 0x5C: MODIFIERS["Win"],
}
KEYS = {key: ord(key) for key in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"}
KEYS.update({f"F{index}": 0x6F + index for index in range(1, 13)})
KEYS.update({"Space": 0x20, "Tab": 0x09, "Enter": 0x0D})
KEY_NAMES = {code: key for key, code in KEYS.items()}
MODIFIER_INPUT = {name.upper(): bit for name, bit in MODIFIERS.items()}
KEY_INPUT = {name.upper(): code for name, code in KEYS.items()}


@dataclass(frozen=True)
class Shortcut:
    modifiers: int
    keys: frozenset[int]

    @classmethod
    def parse(cls, text: str):
        parts = [part.strip().upper() for part in text.split("+")]
        if len(parts) != len(set(parts)) or any(not part for part in parts):
            raise ValueError("请用 + 分隔按键，每个按键只填写一次。")

        modifiers = 0
        keys = set()
        for part in parts:
            if part in MODIFIER_INPUT:
                modifiers |= MODIFIER_INPUT[part]
            elif part in KEY_INPUT:
                keys.add(KEY_INPUT[part])
            else:
                raise ValueError(f"无法识别按键 {part}。")
        if not modifiers or not keys:
            raise ValueError("请填写修饰键和普通键，比如 Ctrl+Shift+S 或 Alt+A+S。")
        return cls(modifiers, frozenset(keys))

    def __str__(self):
        names = [name for name, bit in MODIFIERS.items() if self.modifiers & bit]
        names.extend(KEY_NAMES[key] for key in sorted(self.keys))
        return "+".join(names)


class ChordState:
    """Track only keys relevant to the configured combination."""

    def __init__(self, shortcut: Shortcut):
        self.shortcut = shortcut
        self.pressed = set()
        self.suppressed = set()
        self.matched = False
        self.relevant = shortcut.keys | MODIFIER_KEYS.keys()

    def process(self, key: int, pressed: bool) -> tuple[bool, bool]:
        if key not in self.relevant:
            return False, False
        if pressed and key in self.pressed:
            return False, key in self.suppressed

        swallowed = key in self.suppressed
        if pressed:
            self.pressed.add(key)
        else:
            self.pressed.discard(key)
            self.suppressed.discard(key)

        modifiers = 0
        for down in self.pressed:
            modifiers |= MODIFIER_KEYS.get(down, 0)
        matched = modifiers == self.shortcut.modifiers and self.shortcut.keys <= self.pressed
        triggered = pressed and matched and not self.matched
        self.matched = matched
        if triggered:
            # Swallow the completing key and its release. In Alt+A+S, this
            # prevents an existing Alt+S binding seeing S if it is last.
            self.suppressed.add(key)
        return triggered, swallowed or triggered


USER32 = ctypes.WinDLL("user32", use_last_error=True)
KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
USER32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
USER32.SetWindowsHookExW.restype = wintypes.HANDLE
USER32.UnhookWindowsHookEx.argtypes = (wintypes.HANDLE,)
USER32.CallNextHookEx.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
USER32.CallNextHookEx.restype = ctypes.c_ssize_t
USER32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
USER32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
KERNEL32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
KERNEL32.GetModuleHandleW.restype = wintypes.HMODULE

WH_KEYBOARD_LL = 13
WM_HOTKEY = 0x0312
KEY_DOWN_MESSAGES = (0x0100, 0x0104)
KEY_UP_MESSAGES = (0x0101, 0x0105)
MOD_NOREPEAT = 0x4000


class GlobalHotkey(QAbstractNativeEventFilter):
    def __init__(self, application, callback):
        super().__init__()
        self.application = application
        self.callback = callback
        self.shortcut = None
        self.registration_id = None
        self.hook = None
        self.chord = None
        self.hook_callback = HOOKPROC(self.on_key)

    @property
    def active(self):
        return self.shortcut is not None

    def register(self, shortcut: Shortcut) -> bool:
        if self.shortcut == shortcut:
            return True
        if len(shortcut.keys) == 1:
            # Keep the previous binding live until Windows accepts the new
            # candidate. Separate IDs avoid replacing or removing it early.
            identifier = 2 if self.registration_id == 1 else 1
            key = next(iter(shortcut.keys))
            if not USER32.RegisterHotKey(None, identifier, shortcut.modifiers | MOD_NOREPEAT, key):
                return False
            self.close()
            self.registration_id = identifier
            self.application.installNativeEventFilter(self)
        else:
            if self.hook is None:
                hook = USER32.SetWindowsHookExW(
                    WH_KEYBOARD_LL, self.hook_callback, KERNEL32.GetModuleHandleW(None), 0
                )
                if not hook:
                    return False
                self.close()
                self.hook = hook
            self.chord = ChordState(shortcut)
        self.shortcut = shortcut
        return True

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type).startswith(b"windows_"):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam == self.registration_id:
                self.callback()
                return True, 0
        return False, 0

    def on_key(self, code, message, data):
        if code == 0 and self.chord is not None and message in KEY_DOWN_MESSAGES + KEY_UP_MESSAGES:
            # The first field of KBDLLHOOKSTRUCT is the virtual-key code.
            key = wintypes.DWORD.from_address(data).value
            triggered, suppressed = self.chord.process(key, message in KEY_DOWN_MESSAGES)
            if triggered:
                # Return from the OS hook before doing any UI or capture work.
                QTimer.singleShot(0, lambda current=self.shortcut: self._dispatch(current))
            if suppressed:
                return 1
        return USER32.CallNextHookEx(None, code, message, data)

    def _dispatch(self, shortcut):
        if self.shortcut == shortcut:
            self.callback()

    def close(self):
        if self.registration_id is not None:
            USER32.UnregisterHotKey(None, self.registration_id)
            self.application.removeNativeEventFilter(self)
            self.registration_id = None
        if self.hook is not None:
            USER32.UnhookWindowsHookEx(self.hook)
            self.hook = None
            self.chord = None
        self.shortcut = None
