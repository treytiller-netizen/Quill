# 🪶 Quill

Voice dictation for macOS that runs on **your** machine. Hold a key, speak, release —
polished text appears in whatever app your cursor is in. A local clone of
[Wispr Flow](https://wisprflow.ai).

**Pipeline:** mic → local Whisper (MLX, on-device, 100+ languages) → Claude cleanup
pass (fillers stripped, grammar fixed, tone matched to the app you're in) → pasted at
your cursor with the clipboard preserved.

## Features

| | |
|---|---|
| **Dictation** | Hold **Right ⌥**, speak, release. Unlimited, on-device transcription. |
| **Hands-free mode** | Double-tap **Right ⌥** to lock recording; tap again (or click the Flow Bar) to finish. |
| **Command Mode** | Select text, hold **Right ⌘**, speak an instruction — *"make this more professional"*, *"turn into bullet points"*. With nothing selected, ask a question and the answer is typed at your cursor. |
| **Flow Bar** | A floating pill above the Dock that follows you across Spaces and full-screen apps: live waveform while recording, pulsing while transcribing, ✓ when text lands. Click it for hands-free dictation. |
| **The Hub** | Quill's main window (click the Dock icon, or menu bar → Open Quill Hub). **Home**: date-grouped transcript feed with copy buttons + a stat rail (total words, WPM, day streak, voice-profile progress). **Insights**: WPM, fixes made by Quill, total words, per-app usage bars, a 26-week streak heatmap, and an AI Voice Profile (archetype, catchphrase, most-used word, peak time) refreshed every 1,000 words. **Dictionary**: add/remove terms with usage counts. |
| **History** | Every transcription saved locally (`~/.quill/history.db`) with duration, powering the WPM stats. Recent five in the menu bar (click to copy). |
| **Personal dictionary** | Names, jargon, and `btw -> by the way` replacements, corrected to your spelling. Edit in the Hub or at `~/.quill/dictionary.txt`. |
| **Context-aware tone** | Quill knows which app you're dictating into: casual in Slack, buttoned-up in Mail, literal in your editor. |

## Install

```sh
cd ~/Documents/coding-projects/Quill
./scripts/build_app.sh
open /Applications/Quill.app
```

Then right-click the Dock icon → **Options → Keep in Dock**.

### Permissions (one time)

On first launch macOS will ask for, or you grant in **System Settings → Privacy &
Security**, all attributed to **Quill**:

1. **Microphone** — prompted automatically on first recording
2. **Accessibility** — lets Quill paste (⌘V) into other apps
3. **Input Monitoring** — lets Quill see the hold-key press/release

Because the app bundle path and identifier never change, these grants survive
rebuilds — you approve them once.

### Claude features (cleanup + Command Mode)

Dock-launched apps don't read your shell profile, so put your key in `~/.quill/env`:

```sh
mkdir -p ~/.quill && echo 'ANTHROPIC_API_KEY=sk-ant-...' > ~/.quill/env && chmod 600 ~/.quill/env
```

Without a key, dictation still works fully — you get raw Whisper transcripts and
Command Mode is disabled.

## Development

```sh
uv run quill                     # run from a terminal (permissions attach to the terminal)
```

Settings (hotkeys, models, Flow Bar colors) live in [quill/config.py](quill/config.py).
First run downloads the Whisper model (~1.6 GB) — the menu bar shows ⏬ until ready.

## Launch at login

System Settings → General → Login Items → **+** → `/Applications/Quill.app`.
