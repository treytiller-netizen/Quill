"""Quill — menu bar + Flow Bar app.

Hold Right Option: dictate (release to insert). Double-tap it: hands-free.
Hold Right Command with text selected: Command Mode (speak an edit instruction).
"""

import logging
import subprocess
import threading
import time

import numpy as np
import rumps
import sounddevice as sd
from AppKit import NSWorkspace
from pynput import keyboard

from . import config, history
from .ai import Brain

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
log = logging.getLogger("quill")

ICON_IDLE = "🪶"
ICON_RECORDING = "🔴"
ICON_WORKING = "⏳"
ICON_LOADING = "⏬"


class Recorder:
    """Captures mono 16 kHz float32 audio; reports live levels to the Flow Bar."""

    def __init__(self, on_level=None) -> None:
        self._frames: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._on_level = on_level

    def _callback(self, indata, *_args) -> None:
        self._frames.append(indata.copy())
        if self._on_level is not None:
            rms = float(np.sqrt(np.mean(indata**2)))
            self._on_level(min(1.0, rms * 14))

    def start(self) -> None:
        self._frames = []
        self._stream = sd.InputStream(
            samplerate=config.SAMPLE_RATE,
            channels=1,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> np.ndarray:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if not self._frames:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self._frames).flatten()


def _pbpaste() -> bytes:
    return subprocess.run(["pbpaste"], capture_output=True).stdout


def _pbcopy(data: bytes) -> None:
    subprocess.run(["pbcopy"], input=data)


def insert_text(text: str) -> None:
    """Paste text into the frontmost app, preserving the user's clipboard."""
    saved = _pbpaste()
    _pbcopy(text.encode("utf-8"))
    time.sleep(0.1)
    kb = keyboard.Controller()
    with kb.pressed(keyboard.Key.cmd):
        kb.press("v")
        kb.release("v")
    time.sleep(0.35)
    _pbcopy(saved)


def capture_selection() -> str:
    """Copy the current selection via ⌘C and return it. Restores the clipboard."""
    saved = _pbpaste()
    _pbcopy(b"")
    kb = keyboard.Controller()
    with kb.pressed(keyboard.Key.cmd):
        kb.press("c")
        kb.release("c")
    time.sleep(0.2)
    selection = _pbpaste().decode("utf-8", errors="replace")
    _pbcopy(saved)
    return selection


def frontmost_app() -> str | None:
    try:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return str(app.localizedName()) if app else None
    except Exception:
        return None


class QuillApp(rumps.App):
    def __init__(self) -> None:
        super().__init__(ICON_LOADING, quit_button="Quit Quill")
        self.brain = Brain()
        self.recorder = Recorder(on_level=self._on_level)
        self.flowbar = None

        self._mode: str | None = None  # None | "dictate" | "command"
        self._handsfree = False
        self._model_ready = False
        self._press_time = 0.0
        self._last_tap = 0.0
        self._selection = ""
        self._target_app: str | None = None
        self._lock = threading.Lock()

        self.status_item = rumps.MenuItem("Loading Whisper model…")
        self.status_item.set_callback(None)
        self.stats_item = rumps.MenuItem(f"This week: {history.words_this_week():,} words")
        self.stats_item.set_callback(None)
        self.recent_menu = rumps.MenuItem("Recent")
        self.history_item = rumps.MenuItem("Open History…", callback=lambda _: history.open_viewer())
        self.cleanup_item = rumps.MenuItem("AI cleanup (Claude)", callback=self._toggle_cleanup)
        self.cleanup_item.state = int(self.brain.cleanup_enabled)
        self.dict_item = rumps.MenuItem("Edit Dictionary…", callback=self._open_dictionary)
        self.menu = [
            self.status_item,
            self.stats_item,
            None,
            self.recent_menu,
            self.history_item,
            None,
            self.cleanup_item,
            self.dict_item,
            None,
        ]
        self._refresh_recent_menu()

        # The Flow Bar needs the AppKit run loop — create it just after launch.
        self._boot_timer = rumps.Timer(self._post_launch, 0.5)
        self._boot_timer.start()

        threading.Thread(target=self._warm_up_model, daemon=True).start()
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.start()

    # --- boot -----------------------------------------------------------------

    def _post_launch(self, timer: rumps.Timer) -> None:
        timer.stop()
        if config.FLOWBAR_ENABLED and self.flowbar is None:
            from .flowbar import FlowBar

            self.flowbar = FlowBar(on_click=self._bar_clicked)

    def _warm_up_model(self) -> None:
        import mlx_whisper

        log.info("Loading Whisper model %s…", config.WHISPER_MODEL)
        silence = np.zeros(config.SAMPLE_RATE // 2, dtype=np.float32)
        mlx_whisper.transcribe(silence, path_or_hf_repo=config.WHISPER_MODEL)
        self._model_ready = True
        self.title = ICON_IDLE
        self.status_item.title = "Ready — hold Right ⌥ to talk, Right ⌘ for commands"
        log.info("Model ready.")

    # --- levels → flow bar --------------------------------------------------------

    def _on_level(self, level: float) -> None:
        if self.flowbar is not None:
            self.flowbar.push_level(level)

    def _bar_state(self, state: str, flash: str = "") -> None:
        if self.flowbar is not None:
            if state == "flash":
                self.flowbar.flash(flash)
            else:
                self.flowbar.set_state(state)

    # --- hotkeys --------------------------------------------------------------

    def _on_press(self, key) -> None:
        if not self._model_ready:
            return
        if key == config.DICTATE_KEY:
            if self._handsfree:
                self._finish_dictation()
            elif self._mode is None:
                self._start(mode="dictate")
        elif key == config.COMMAND_KEY and self._mode is None:
            self._start(mode="command")
            threading.Thread(target=self._grab_selection, daemon=True).start()

    def _on_release(self, key) -> None:
        if key == config.DICTATE_KEY and self._mode == "dictate" and not self._handsfree:
            duration = time.time() - self._press_time
            if duration < config.TAP_MAX_SECONDS:
                if self._press_time - self._last_tap < config.DOUBLE_TAP_SECONDS:
                    self._handsfree = True  # double-tap → keep recording hands-free
                    return
                self._last_tap = self._press_time
                self._abort_recording()
                return
            self._finish_dictation()
        elif key == config.COMMAND_KEY and self._mode == "command":
            self._finish_command()

    def _bar_clicked(self) -> None:
        """Click the Flow Bar: toggle hands-free dictation."""
        if not self._model_ready:
            return
        if self._mode == "dictate":
            self._finish_dictation()
        elif self._mode is None:
            self._start(mode="dictate")
            self._handsfree = True

    # --- recording lifecycle ---------------------------------------------------------

    def _start(self, mode: str) -> None:
        with self._lock:
            if self._mode is not None:
                return
            self._mode = mode
        self._press_time = time.time()
        self._target_app = frontmost_app()
        self._selection = ""
        try:
            self.recorder.start()
            self.title = ICON_RECORDING
            self._bar_state("recording" if mode == "dictate" else "command")
        except Exception as exc:
            log.error("Could not open microphone: %s", exc)
            self._mode = None
            self.title = ICON_IDLE
            self._bar_state("flash", "⚠️ mic unavailable")

    def _grab_selection(self) -> None:
        self._selection = capture_selection()

    def _abort_recording(self) -> None:
        self.recorder.stop()
        self._mode = None
        self._handsfree = False
        self.title = ICON_IDLE
        self._bar_state("idle")

    def _finish_dictation(self) -> None:
        audio = self.recorder.stop()
        self._mode = None
        self._handsfree = False
        self.title = ICON_WORKING
        self._bar_state("working")
        threading.Thread(target=self._process_dictation, args=(audio,), daemon=True).start()

    def _finish_command(self) -> None:
        audio = self.recorder.stop()
        self._mode = None
        self.title = ICON_WORKING
        self._bar_state("working")
        threading.Thread(target=self._process_command, args=(audio,), daemon=True).start()

    # --- pipelines ----------------------------------------------------------------

    def _transcribe(self, audio: np.ndarray) -> str:
        if len(audio) < config.SAMPLE_RATE * config.MIN_RECORDING_SECONDS:
            return ""
        import mlx_whisper

        result = mlx_whisper.transcribe(audio, path_or_hf_repo=config.WHISPER_MODEL)
        return result["text"].strip()

    def _process_dictation(self, audio: np.ndarray) -> None:
        try:
            transcript = self._transcribe(audio)
            if not transcript:
                self._bar_state("idle")
                return
            text = self.brain.clean(transcript, self._target_app)
            insert_text(text)
            history.add(transcript, text, self._target_app, mode="dictate")
            self._after_insert(text)
        except Exception as exc:
            log.error("Dictation failed: %s", exc)
            self._bar_state("flash", "✗ failed")
        finally:
            self.title = ICON_IDLE

    def _process_command(self, audio: np.ndarray) -> None:
        try:
            instruction = self._transcribe(audio)
            if not instruction:
                self._bar_state("idle")
                return
            if not self.brain.available:
                self._bar_state("flash", "⚠️ needs API key")
                return
            result = self.brain.command(instruction, self._selection, self._target_app)
            if result is None:
                self._bar_state("flash", "✗ command failed")
                return
            insert_text(result)
            history.add(instruction, result, self._target_app, mode="command")
            self._after_insert(result)
        except Exception as exc:
            log.error("Command mode failed: %s", exc)
            self._bar_state("flash", "✗ failed")
        finally:
            self.title = ICON_IDLE

    def _after_insert(self, text: str) -> None:
        self._bar_state("flash", "✓ inserted")
        self.stats_item.title = f"This week: {history.words_this_week():,} words"
        self._refresh_recent_menu()

    # --- menu -----------------------------------------------------------------

    def _refresh_recent_menu(self) -> None:
        # rumps MenuItem has no underlying NSMenu until an item is added;
        # clear() on a virgin submenu raises AttributeError.
        if self.recent_menu._menu is not None:
            self.recent_menu.clear()
        rows = history.recent(5)
        if not rows:
            empty = rumps.MenuItem("Nothing yet")
            empty.set_callback(None)
            self.recent_menu.add(empty)
            return
        for _ts, _app, text in rows:
            label = text.replace("\n", " ")
            if len(label) > 60:
                label = label[:57] + "…"
            item = rumps.MenuItem(label, callback=self._copy_recent)
            item._full_text = text
            self.recent_menu.add(item)

    @staticmethod
    def _copy_recent(item: rumps.MenuItem) -> None:
        _pbcopy(item._full_text.encode("utf-8"))

    def _toggle_cleanup(self, item: rumps.MenuItem) -> None:
        self.brain.cleanup_enabled = not self.brain.cleanup_enabled
        item.state = int(self.brain.cleanup_enabled)

    def _open_dictionary(self, _item: rumps.MenuItem) -> None:
        config.CONFIG_DIR.mkdir(exist_ok=True)
        if not config.DICTIONARY_FILE.exists():
            config.DICTIONARY_FILE.write_text(
                "# One term per line: names, jargon, product names.\n"
                "# Quill corrects misheard words to these spellings.\n"
            )
        subprocess.run(["open", "-t", str(config.DICTIONARY_FILE)])


def main() -> None:
    QuillApp().run()


if __name__ == "__main__":
    main()
