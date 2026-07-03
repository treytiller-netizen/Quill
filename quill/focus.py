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


def text_input_focused() -> bool:
    """True when the focused UI element accepts text (field, area, editor).

    Queries the frontmost app's focused element — the system-wide
    AXFocusedUIElement query returns kAXErrorCannotComplete on modern macOS.
    """
    try:
        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        if front is None:
            return False
        ax_app = AXUIElementCreateApplication(front.processIdentifier())
        element = _attr(ax_app, "AXFocusedUIElement")
        if element is None:
            return False
        role = _attr(element, "AXRole")
        if role in _TEXT_ROLES:
            return True
        # Editors in Electron/web views often report other roles but expose an
        # editable selection — treat a settable AXSelectedText as "text input".
        err, settable = AXUIElementIsAttributeSettable(element, "AXSelectedText", None)
        return err == 0 and bool(settable)
    except Exception:
        log.exception("Focus detection failed")
        return False
