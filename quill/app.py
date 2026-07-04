"""Quill — menu bar + Flow Bar + Dock app.

Hold Right Option: dictate (release to insert). Double-tap it: hands-free.
Hold Right Command with text selected: Command Mode (speak an edit instruction).
"""

import fcntl
import logging
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import rumps
import sounddevice as sd
from AppKit import NSWorkspace

from . import config, focus, history, keys, window
from .ai import Brain

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
log = logging.getLogger("quill")

ICON_IDLE = "🪶"
ICON_RECORDING = "🔴"
ICON_WORKING = "⏳"
ICON_LOADING = "⏬"

INPUT_MONITORING_PANE = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
)


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
    """Paste text into the frontmost app, preserving the user's clipboard.

    Returns right after the paste lands; the clipboard restore happens on a
    background timer so the caller (and the ✓ flash) isn't held up by it.
    """
    saved = _pbpaste()
    _pbcopy(text.encode("utf-8"))
    time.sleep(0.05)
    keys.paste()

    def restore() -> None:
        time.sleep(0.8)  # give the target app time to read the clipboard
        _pbcopy(saved)

    threading.Thread(target=restore, daemon=True).start()


def capture_selection() -> str:
    """Copy the current selection via ⌘C and return it. Restores the clipboard."""
    saved = _pbpaste()
    _pbcopy(b"")
    keys.copy()
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
        self.hotkeys = keys.HotkeyTap(
            {
                config.DICTATE_KEYCODE: (
                    config.DICTATE_MASK,
                    self._dictate_pressed,
                    self._dictate_released,
                ),
                config.COMMAND_KEYCODE: (
                    config.COMMAND_MASK,
                    self._command_pressed,
                    self._command_released,
                ),
            }
        )

        self._mode: str | None = None  # None | "dictate" | "command"
        self._handsfree = False
        self._model_ready = False
        self._hotkeys_ready = False
        self._press_time = 0.0
        self._last_tap = 0.0
        self._selection = ""
        self._target_app: str | None = None
        self._lock = threading.Lock()
        # ALL CoreAudio stream operations run on this single worker, in order.
        # Starting/stopping the stream on the main thread — inside the event
        # tap callback — deadlocks CoreAudio (HALB_Mutex vs the main run loop).
        self._audio_exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="audio")

        self.status_item = rumps.MenuItem("Loading Whisper model…")
        self.status_item.set_callback(None)
        self.stats_item = rumps.MenuItem(f"This week: {history.words_this_week():,} words")
        self.stats_item.set_callback(None)
        window.init(self.brain)
        self.recent_menu = rumps.MenuItem("Recent")
        self.history_item = rumps.MenuItem("Open Quill Hub…", callback=lambda _: window.show_hub())
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

        # Everything touching the AppKit run loop happens just after launch.
        self._boot_timer = rumps.Timer(self._post_launch, 0.5)
        self._boot_timer.start()

        threading.Thread(target=self._warm_up_model, daemon=True).start()

    # --- boot -----------------------------------------------------------------

    def _post_launch(self, timer: rumps.Timer) -> None:
        timer.stop()
        # Behave like a regular desktop app: Dock icon with a running indicator,
        # right-click → Quit, ⌘Q.
        from AppKit import NSApplication, NSApplicationActivationPolicyRegular

        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyRegular
        )

        # Trigger the one-time Accessibility prompt (needed for pasting).
        try:
            from ApplicationServices import (
                AXIsProcessTrustedWithOptions,
                kAXTrustedCheckOptionPrompt,
            )

            AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
        except Exception:
            log.exception("Accessibility trust check failed")

        if config.FLOWBAR_ENABLED and self.flowbar is None:
            from .flowbar import FlowBar

            self.flowbar = FlowBar(on_click=self._bar_clicked)

        # Hotkey tap needs Input Monitoring; installing it triggers the prompt.
        # Keep retrying until the user grants it.
        if not self._try_install_hotkeys():
            self._tap_timer = rumps.Timer(self._retry_hotkeys, 2)
            self._tap_timer.start()

    def _try_install_hotkeys(self) -> bool:
        if self.hotkeys.install():
            self._hotkeys_ready = True
            self._update_status()
            return True
        self.status_item.title = "⚠️ Grant Input Monitoring (click to open)"
        self.status_item.set_callback(
            lambda _: subprocess.run(["open", INPUT_MONITORING_PANE])
        )
        return False

    def _retry_hotkeys(self, timer: rumps.Timer) -> None:
        if self._try_install_hotkeys():
            timer.stop()

    def _warm_up_model(self) -> None:
        import mlx_whisper

        ready_sentinel = config.CONFIG_DIR / "model_ready"
        ready_sentinel.unlink(missing_ok=True)
        log.info("Loading Whisper model %s…", config.WHISPER_MODEL)
        silence = np.zeros(config.SAMPLE_RATE // 2, dtype=np.float32)
        mlx_whisper.transcribe(silence, path_or_hf_repo=config.WHISPER_MODEL)
        self._model_ready = True
        self.title = ICON_IDLE
        self._update_status()
        ready_sentinel.touch()
        log.info("Model ready.")

    def _update_status(self) -> None:
        if not self._model_ready:
            return
        if self._hotkeys_ready:
            self.status_item.title = "Ready — hold Right ⌥ to talk, Right ⌘ for commands"
            self.status_item.set_callback(None)

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

    def _dictate_pressed(self) -> None:
        if not self._model_ready:
            return
        if self._handsfree:
            self._finish_dictation()
        elif self._mode is None:
            self._start(mode="dictate")

    def _dictate_released(self) -> None:
        if self._mode != "dictate" or self._handsfree:
            return
        duration = time.time() - self._press_time
        if duration < config.TAP_MAX_SECONDS:
            if self._press_time - self._last_tap < config.DOUBLE_TAP_SECONDS:
                self._handsfree = True  # double-tap → keep recording hands-free
                return
            self._last_tap = self._press_time
            self._abort_recording()
            return
        self._finish_dictation()

    def _command_pressed(self) -> None:
        if self._model_ready and self._mode is None:
            self._start(mode="command")
            threading.Thread(target=self._grab_selection, daemon=True).start()

    def _command_released(self) -> None:
        if self._mode == "command":
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
        self.title = ICON_RECORDING
        self._bar_state("recording" if mode == "dictate" else "command")

        def start_stream() -> None:
            try:
                self.recorder.start()
            except Exception as exc:
                log.error("Could not open microphone: %s", exc)
                self._mode = None
                self.title = ICON_IDLE
                self._bar_state("flash", "⚠️ mic unavailable")

        self._audio_exec.submit(start_stream)

    def _grab_selection(self) -> None:
        self._selection = capture_selection()

    def _abort_recording(self) -> None:
        self._mode = None
        self._handsfree = False
        self.title = ICON_IDLE
        self._bar_state("idle")
        self._audio_exec.submit(self.recorder.stop)

    def _finish_dictation(self) -> None:
        self._mode = None
        self._handsfree = False
        self.title = ICON_WORKING
        self._bar_state("working")

        def stop_and_process() -> None:
            audio = self.recorder.stop()
            threading.Thread(
                target=self._process_dictation, args=(audio,), daemon=True
            ).start()

        self._audio_exec.submit(stop_and_process)

    def _finish_command(self) -> None:
        self._mode = None
        self.title = ICON_WORKING
        self._bar_state("working")

        def stop_and_process() -> None:
            audio = self.recorder.stop()
            threading.Thread(
                target=self._process_command, args=(audio,), daemon=True
            ).start()

        self._audio_exec.submit(stop_and_process)

    # --- pipelines ----------------------------------------------------------------

    def _transcribe(self, audio: np.ndarray) -> str:
        if len(audio) < config.SAMPLE_RATE * config.MIN_RECORDING_SECONDS:
            return ""
        import mlx_whisper

        result = mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=config.WHISPER_MODEL,
            language=config.WHISPER_LANGUAGE,
            **config.WHISPER_DECODE_OPTIONS,
        )
        # Return MLX's cached Metal buffers to the OS — on an 8 GB machine a
        # long dictation otherwise leaves hundreds of MB parked on the GPU
        # cache and pushes the system into swap.
        try:
            import mlx.core as mx

            mx.clear_cache()
        except Exception:
            pass
        return result["text"].strip()

    def _process_dictation(self, audio: np.ndarray) -> None:
        try:
            transcript = self._transcribe(audio)
            if not transcript:
                self._bar_state("idle")
                return
            text = self.brain.clean(transcript, self._target_app)
            self._deliver(text)
            history.add(transcript, text, self._target_app, mode="dictate",
                        duration=len(audio) / config.SAMPLE_RATE)
            self._after_insert()
        except Exception as exc:
            log.error("Dictation failed: %s", exc)
            self._bar_state("flash", "✗ failed")
        finally:
            self.title = ICON_IDLE

    def _deliver(self, text: str) -> None:
        """Insert into the focused text input; otherwise copy to the clipboard.

        Mirrors Wispr Flow: paste only lands somewhere real. Without a focused
        input (or without Accessibility permission), the text goes to the
        clipboard and the Flow Bar says so.
        """
        if not focus.trusted():
            _pbcopy(text.encode("utf-8"))
            log.warning("Accessibility not granted — copied instead of pasting")
            self._bar_state("flash", "📋 Copied — enable Accessibility to insert")
            return
        if focus.text_input_focused():
            insert_text(text)
            self._bar_state("flash", "✓ inserted")
        else:
            _pbcopy(text.encode("utf-8"))
            self._bar_state("flash", "📋 Copied — press ⌘V")

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
            self._deliver(result)
            history.add(instruction, result, self._target_app, mode="command",
                        duration=len(audio) / config.SAMPLE_RATE)
            self._after_insert()
        except Exception as exc:
            log.error("Command mode failed: %s", exc)
            self._bar_state("flash", "✗ failed")
        finally:
            self.title = ICON_IDLE

    def _after_insert(self) -> None:
        self.stats_item.title = f"This week: {history.words_this_week():,} words"
        self._refresh_recent_menu()
        window.maybe_refresh_voice_profile()

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


RUN_MARKER = config.CONFIG_DIR / "should_run"
_lock_file = None  # held for the process lifetime


def _acquire_single_instance() -> bool:
    """Only one Quill at a time (the watchdog and Dock can race at wake)."""
    global _lock_file
    config.CONFIG_DIR.mkdir(exist_ok=True)
    _lock_file = open(config.CONFIG_DIR / "quill.lock", "w")
    try:
        fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _install_dock_delegate() -> None:
    """Dock-icon clicks open the Hub; a user-intended Quit clears the run
    marker so the watchdog knows not to resurrect us."""
    import rumps.rumps as _rumps_internal

    class QuillDelegate(_rumps_internal.NSApp):
        def applicationShouldHandleReopen_hasVisibleWindows_(self, _sender, _flag):
            try:
                window.show_hub()
            except Exception as exc:
                log.error("Could not open Hub on Dock click: %s", exc)
            return False

        def applicationShouldTerminate_(self, _sender):
            try:
                RUN_MARKER.unlink(missing_ok=True)
            except Exception:
                log.exception("Could not clear run marker")
            return 1  # NSTerminateNow

    _rumps_internal.NSApp = QuillDelegate


def main() -> None:
    if not _acquire_single_instance():
        log.info("Quill is already running — exiting")
        sys.exit(0)
    RUN_MARKER.touch()  # watchdog revives us while this exists
    _install_dock_delegate()
    QuillApp().run()


if __name__ == "__main__":
    main()
