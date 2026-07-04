"""User-tweakable settings for Quill."""

from pathlib import Path

# --- Hotkeys ----------------------------------------------------------------
# Virtual keycode + device modifier mask pairs (see quill/keys.py).
# Hold to dictate; release to transcribe + insert.
# Double-tap quickly for hands-free mode (tap again to stop).
DICTATE_KEYCODE = 61      # Right Option
DICTATE_MASK = 0x0040     # NX_DEVICERALTKEYMASK

# Hold to use Command Mode: select text first, then hold and speak an
# instruction ("make this more professional", "turn into bullet points").
# With nothing selected, it answers/generates and inserts the result.
COMMAND_KEYCODE = 54      # Right Command
COMMAND_MASK = 0x0010     # NX_DEVICERCMDKEYMASK

DOUBLE_TAP_SECONDS = 0.5   # max gap between taps to trigger hands-free mode
TAP_MAX_SECONDS = 0.35     # a press shorter than this counts as a "tap"

# --- Transcription ----------------------------------------------------------
# Any MLX-converted Whisper repo on Hugging Face. On this 8 GB machine
# (benchmarked, loaded system): small ≈ 0.8s/sentence and ~4s per 45s ramble
# with very good clear-speech accuracy; large-turbo-q4 ≈ 2.5-3.5s/sentence
# with best-in-class accuracy. Trey picked small for speed (2026-07-04).
# More accurate: "mlx-community/whisper-large-v3-turbo-q4"
# Even faster:   "mlx-community/whisper-base-mlx"
WHISPER_MODEL = "mlx-community/whisper-small-mlx"
SAMPLE_RATE = 16_000
MIN_RECORDING_SECONDS = 0.4  # ignore accidental taps

# Pinning the language skips a per-dictation detection pass (~35% faster).
# Set to None to restore automatic detection across 100+ languages.
WHISPER_LANGUAGE = "en"

# Single greedy decode pass — no temperature-fallback retry cascade, no
# cross-segment conditioning. Benchmarked identical output at ~0.6x the time.
WHISPER_DECODE_OPTIONS = dict(
    temperature=0.0,
    condition_on_previous_text=False,
    compression_ratio_threshold=None,
    logprob_threshold=None,
)

# --- AI (cleanup + Command Mode) ---------------------------------------------
# Uses the Anthropic API when credentials are available (ANTHROPIC_API_KEY,
# usually via ~/.quill/env for Dock launches). Cleanup degrades gracefully to
# the raw transcript; Command Mode requires credentials.
CLEANUP_ENABLED = True
# Haiku for the per-dictation work (cleanup + Command Mode): ~5x cheaper and
# snappier than Opus, plenty for text polishing (~0.1¢ per dictation).
CLEANUP_MODEL = "claude-haiku-4-5"
# Opus for the Voice Profile: runs ~once per 1,000 words, cost is negligible,
# and the personality-analysis quality is worth it.
VOICE_MODEL = "claude-opus-4-8"
CLAUDE_TIMEOUT_SECONDS = 20.0

# --- Data --------------------------------------------------------------------
CONFIG_DIR = Path.home() / ".quill"
DICTIONARY_FILE = CONFIG_DIR / "dictionary.txt"
HISTORY_DB = CONFIG_DIR / "history.db"

# --- Flow bar ----------------------------------------------------------------
FLOWBAR_ENABLED = True
# RGBA 0-1. Branding: warm paper + ink, coral accent.
BAR_BACKGROUND = (0.09, 0.09, 0.11, 0.94)
BAR_ACCENT = (1.00, 0.45, 0.25, 1.0)     # dictation coral
BAR_COMMAND = (0.55, 0.55, 1.00, 1.0)    # command-mode periwinkle
BAR_TEXT = (0.96, 0.94, 0.90, 1.0)
BAR_BOTTOM_MARGIN = 10  # px above the bottom of the visible screen area
