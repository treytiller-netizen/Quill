"""Focused-element detection via the Accessibility API.

Used to decide delivery: if a text input is focused in the frontmost window,
paste into it; otherwise leave the text on the clipboard and say so.
"""

import logging

from AppKit import NSWorkspace
from ApplicationServices import (
    AXIsProcessTrusted,
    AXUIElementCopyAttributeValue,
    AXUIElementCreateApplication,
    AXUIElementIsAttributeSettable,
)

log = logging.getLogger("quill.focus")

_TEXT_ROLES = {
    "AXTextField",
    "AXTextArea",
    "AXComboBox",
    "AXSearchField",
}


def trusted() -> bool:
    """Whether Quill has Accessibility permission (required to paste)."""
    return bool(AXIsProcessTrusted())


def _attr(element, name):
    err, value = AXUIElementCopyAttributeValue(element, name, None)
    return value if err == 0 else None


def frontmost_pid() -> int | None:
    try:
        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        return int(front.processIdentifier()) if front else None
    except Exception:
        return None


def text_input_focused(pid: int | None = None) -> bool:
    """True when the focused UI element accepts text (field, area, editor).

    Pass the pid captured when recording started — asking "what's frontmost"
    from a worker thread mid-processing is unreliable, and the dictation
    should target the app the user was in when they pressed the key anyway.
    (The system-wide AXFocusedUIElement query returns kAXErrorCannotComplete
    on modern macOS, hence the per-app query.)
    """
    try:
        if pid is None:
            pid = frontmost_pid()
        if pid is None:
            log.info("Focus probe: no frontmost app")
            return False
        ax_app = AXUIElementCreateApplication(pid)
        element = _attr(ax_app, "AXFocusedUIElement")
        if element is None:
            log.info("Focus probe: pid %s has no focused element", pid)
            return False
        role = _attr(element, "AXRole")
        if role in _TEXT_ROLES:
            return True
        # Editors in Electron/web views often report other roles but expose an
        # editable selection — treat a settable AXSelectedText as "text input".
        err, settable = AXUIElementIsAttributeSettable(element, "AXSelectedText", None)
        log.info("Focus probe: pid %s role %s settable=%s", pid, role, bool(settable))
        return err == 0 and bool(settable)
    except Exception:
        log.exception("Focus detection failed")
        return False
