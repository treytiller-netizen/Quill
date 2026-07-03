"""The Flow Bar: a small floating pill pinned above the bottom of the screen.

Idle it's a discreet feather chip; while recording it expands into a live
waveform; it pulses while transcribing and flashes a confirmation when text
lands. It joins all Spaces and full-screen apps, and never steals focus
(non-activating panel), so the paste target keeps keyboard focus. Clicking it
toggles hands-free dictation.
"""

import math
import time
from collections import deque

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSMakeRect,
    NSPanel,
    NSScreen,
    NSStatusWindowLevel,
    NSView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from Foundation import NSObject, NSString, NSThread, NSTimer

from . import config

# Panel sizes per state: (width, height)
_SIZES = {
    "idle": (56, 24),
    "recording": (220, 34),
    "command": (220, 34),
    "working": (110, 30),
    "flash": (150, 30),
}


class _MainThread(NSObject):
    """Run arbitrary Python callables on the AppKit main thread."""

    def run_(self, fn):
        fn()


_dispatcher = None


def _on_main(fn, wait: bool = False) -> None:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = _MainThread.alloc().init()
    if NSThread.isMainThread():
        fn()
    else:
        _dispatcher.performSelectorOnMainThread_withObject_waitUntilDone_("run:", fn, wait)


def _color(rgba) -> NSColor:
    return NSColor.colorWithSRGBRed_green_blue_alpha_(*rgba)


class _BarView(NSView):
    """Draws the pill for the current state."""

    def initWithFrame_(self, frame):
        self = objc.super(_BarView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.state = "idle"
        self.levels = deque([0.0] * 28, maxlen=28)
        self.flash_text = ""
        self.on_click = None
        self._timer = None
        return self

    def acceptsFirstMouse_(self, event):  # click works without focusing us first
        return True

    def mouseDown_(self, event):
        if self.on_click is not None:
            self.on_click()

    # -- animation timer -------------------------------------------------------

    def startAnimating(self):
        if self._timer is None:
            self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                1 / 30.0, self, "tick:", None, True
            )

    def stopAnimating(self):
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None
        self.setNeedsDisplay_(True)

    def tick_(self, _timer):
        self.setNeedsDisplay_(True)

    # -- drawing ----------------------------------------------------------------

    def drawRect_(self, _rect):
        bounds = self.bounds()
        w, h = bounds.size.width, bounds.size.height

        pill = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            bounds, h / 2, h / 2
        )
        _color(config.BAR_BACKGROUND).setFill()
        pill.fill()

        if self.state in ("recording", "command"):
            accent = config.BAR_ACCENT if self.state == "recording" else config.BAR_COMMAND
            self._draw_waveform(w, h, accent)
        elif self.state == "working":
            self._draw_dots(w, h)
        elif self.state == "flash":
            self._draw_text(self.flash_text, w, h, 12)
        else:  # idle
            self._draw_text("🪶", w, h, 13)

    def _draw_waveform(self, w, h, accent):
        levels = list(self.levels)
        n = len(levels)
        pad, gap = 14.0, 3.0
        bar_w = (w - 2 * pad - gap * (n - 1)) / n
        _color(accent).setFill()
        t = time.time()
        for i, level in enumerate(levels):
            # tiny idle shimmer so the bar reads as "live" even in silence
            shimmer = 0.06 + 0.03 * math.sin(t * 6 + i * 0.9)
            frac = max(shimmer, min(1.0, level))
            bh = max(2.0, (h - 12) * frac)
            x = pad + i * (bar_w + gap)
            y = (h - bh) / 2
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(x, y, bar_w, bh), bar_w / 2, bar_w / 2
            ).fill()

    def _draw_dots(self, w, h):
        t = time.time()
        _color(config.BAR_TEXT).setFill()
        for i in range(3):
            phase = (math.sin(t * 5 - i * 0.9) + 1) / 2
            r = 2.2 + 1.6 * phase
            x = w / 2 + (i - 1) * 14 - r
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(x, h / 2 - r, r * 2, r * 2)
            ).fill()

    def _draw_text(self, text, w, h, size):
        attrs = {
            NSFontAttributeName: NSFont.systemFontOfSize_(size),
            NSForegroundColorAttributeName: _color(config.BAR_TEXT),
        }
        s = NSString.stringWithString_(text)
        box = s.sizeWithAttributes_(attrs)
        s.drawAtPoint_withAttributes_(
            (w / 2 - box.width / 2, h / 2 - box.height / 2), attrs
        )


class FlowBar:
    """Thread-safe facade around the panel. All methods may be called from any thread."""

    def __init__(self, on_click=None) -> None:
        self._panel = None
        self._view = None
        self._on_click = on_click
        self._flash_generation = 0
        _on_main(self._build, wait=True)

    def _build(self) -> None:
        w, h = _SIZES["idle"]
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, w, h),
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered,
            False,
        )
        panel.setLevel_(NSStatusWindowLevel)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setMovableByWindowBackground_(False)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
        )
        view = _BarView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
        view.on_click = self._on_click
        panel.setContentView_(view)
        self._panel, self._view = panel, view
        self._position("idle")
        panel.orderFrontRegardless()

    def _position(self, state: str) -> None:
        w, h = _SIZES[state]
        screen = NSScreen.mainScreen()
        if screen is None:
            return
        vis = screen.visibleFrame()
        x = vis.origin.x + (vis.size.width - w) / 2
        y = vis.origin.y + config.BAR_BOTTOM_MARGIN
        self._panel.setFrame_display_(NSMakeRect(x, y, w, h), True)

    # -- public API -----------------------------------------------------------

    def set_state(self, state: str, flash_text: str = "") -> None:
        def apply():
            self._flash_generation += 1
            self._view.state = state
            self._view.flash_text = flash_text
            self._position(state)
            if state in ("recording", "command", "working"):
                self._view.startAnimating()
            else:
                self._view.stopAnimating()
            self._panel.orderFrontRegardless()

        _on_main(apply)

    def push_level(self, level: float) -> None:
        if self._view is not None:
            self._view.levels.append(level)

    def flash(self, text: str, seconds: float = 1.3) -> None:
        self.set_state("flash", text)

        def revert_later():
            generation = self._flash_generation

            def revert(_timer=None):
                if self._flash_generation == generation:  # nothing newer happened
                    self.set_state("idle")

            NSTimer.scheduledTimerWithTimeInterval_repeats_block_(seconds, False, revert)

        _on_main(revert_later)
