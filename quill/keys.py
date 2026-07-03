"""Native hotkey listening + synthetic keystrokes via Quartz.

Why not pynput: its listener thread calls Text Input Services (TIS) APIs
concurrently with AppKit's main thread, which macOS treats as a fatal error
(SIGABRT: "TIS/TSM API is being called in two threads concurrently").

Instead we use a listen-only CGEventTap for `flagsChanged` events, installed on
the MAIN run loop (no extra thread), and post synthetic key events with fixed
virtual keycodes (no TIS layout lookups). The tap needs Input Monitoring;
CGEventPost needs Accessibility.
"""

import logging

import Quartz

log = logging.getLogger("quill.keys")

# ANSI virtual keycodes
VK_ALT_RIGHT = 61
VK_CMD_RIGHT = 54
VK_C = 8
VK_V = 9

# Device-specific modifier bits (NX_DEVICER*KEYMASK) — distinguish right from left
MASK_ALT_RIGHT = 0x0040
MASK_CMD_RIGHT = 0x0010

CMD_FLAG = Quartz.kCGEventFlagMaskCommand


def press_combo(keycode: int, flags: int = 0) -> None:
    """Post a synthetic key down+up with the given modifier flags."""
    for down in (True, False):
        event = Quartz.CGEventCreateKeyboardEvent(None, keycode, down)
        Quartz.CGEventSetFlags(event, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def paste() -> None:
    press_combo(VK_V, CMD_FLAG)


def copy() -> None:
    press_combo(VK_C, CMD_FLAG)


class HotkeyTap:
    """Listen-only event tap for modifier hold/release, on the main run loop.

    bindings: {keycode: (device_mask, on_press, on_release)}
    Call install() from the main thread. Returns False while the app lacks
    Input Monitoring permission — retry later.
    """

    def __init__(self, bindings) -> None:
        self._bindings = bindings
        self._held: set[int] = set()
        self._tap = None

    def install(self) -> bool:
        if self._tap is not None:
            return True
        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged),
            self._handle,
            None,
        )
        if tap is None:  # Input Monitoring not granted (yet)
            return False
        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        Quartz.CFRunLoopAddSource(
            Quartz.CFRunLoopGetMain(), source, Quartz.kCFRunLoopCommonModes
        )
        Quartz.CGEventTapEnable(tap, True)
        self._tap = tap
        log.info("Hotkey event tap installed")
        return True

    def _handle(self, _proxy, event_type, event, _refcon):
        try:
            if event_type == Quartz.kCGEventTapDisabledByTimeout:
                Quartz.CGEventTapEnable(self._tap, True)
                return event
            keycode = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventKeycode
            )
            binding = self._bindings.get(keycode)
            if binding is None:
                return event
            mask, on_press, on_release = binding
            pressed = bool(Quartz.CGEventGetFlags(event) & mask)
            if pressed and keycode not in self._held:
                self._held.add(keycode)
                on_press()
            elif not pressed and keycode in self._held:
                self._held.discard(keycode)
                on_release()
        except Exception:
            log.exception("Hotkey handler error")
        return event
