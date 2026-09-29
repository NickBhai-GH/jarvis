# Jarvis

A voice assistant for your Linux desktop that talks like J.A.R.V.I.S. from the Iron Man films. Say **"Hey Jarvis"**, wait for the chime, talk. The brain is a long-running Claude Code session (through the Claude Agent SDK), so it can do anything Claude Code can: run commands, edit files, search the web, drive Chrome. On top of that it gets its own desktop tools: open apps, focus windows, take screenshots, click, scroll, type, control media and volume.

How it fits together:

- `ears.py`: microphone, "Hey Jarvis" wake word (openWakeWord), end-of-speech detection, speech to text (faster-whisper).
- `brain.py`: one persistent Claude Code session, the persona, and the safety gate that asks you out loud before risky actions.
- `mouth.py`: text to speech (Kokoro by default, or a Piper voice), cut off the moment you talk over it.
- `pctools.py` + `kwin.py`: the desktop tools, served to Claude as an in-process MCP server called `jarvis`.
- `jarvis.py`: the main loop. `dashboard.py` + `dashboard.html`: a local web dashboard. `widget.py`: a small on-screen status panel. `events.py`: the activity log.
- `persona.md`: the system prompt, a template filled in from your config. `lines.md`: real JARVIS lines by situation, used to tune the voice.

## Requirements

- Linux with **KDE Plasma 6 on Wayland**. Desktop control currently targets KWin only (window list, focus and cursor go through KWin scripts, input through ydotool). The voice and Claude parts would work elsewhere, the desktop tools would not.
- **Claude Code** installed and logged in (`claude`, then log in with your Claude subscription). Jarvis uses your normal Claude Code login, settings, memory and MCP servers.
- A microphone. Headphones are recommended if you want to interrupt it by talking over it.
- Python 3.12 (what it is tested on).
- System tools: `ydotool` (and write access to `/dev/uinput`, usually via a udev rule or the `input` group), `spectacle`, `qdbus`, `busctl`, `wpctl`, `pactl`, `notify-send`, `gtk-launch`, `xdg-open`, and PortAudio for `sounddevice`. Most are already there on a KDE desktop.
- Optional: the Claude in Chrome extension, so it can work in your browser.

## Install

```
git clone <this repo> ~/jarvis
cd ~/jarvis
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Kokoro voice model
mkdir -p models
curl -L -o models/kokoro-v1.0.onnx https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
curl -L -o models/voices-v1.0.bin https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin

# "Hey Jarvis" wake word and voice detection models
.venv/bin/python -c "import openwakeword.utils as u; u.download_models(['hey_jarvis'])"
```

The whisper models download by themselves on first run.

## Configure

Settings live in `config.py`. Put your own values in `config_local.py` (it is gitignored and overrides `config.py`), for example:

```
USER_NAME = "Pepper"
HONORIFIC = "ma'am"
CITY = "Manchester"
WRITE_OK_DIRS = ["/home/pepper/projects"]
VOCAB = "Jarvis, Pepper, Manchester, Spotify, Discord, Konsole."
```

| Setting | What it does |
|---|---|
| `USER_NAME` | Your name, as Jarvis refers to you |
| `HONORIFIC` | How it addresses you: "sir", "ma'am", "boss" |
| `CITY` | City for the weather line in the morning greeting (wttr.in, use + for spaces) |
| `WRITE_OK_DIRS` | Folders outside your home it may write to without asking |
| `VOCAB` | Names and words whisper keeps mishearing |
| `VOICE` | Kokoro voice: bm_lewis, bm_george, bm_daniel, bm_fable |
| `VOICE_ENGINE` / `PIPER_MODEL` | Switch to your own Piper voice (see below) |
| `MODEL` / `EFFORT` | Claude model and effort level |
| `WAKE_THRESHOLD` | Raise if it wakes on its own, lower if it ignores you |
| `END_SILENCE_S` | Raise if it cuts you off mid-sentence |
| `MIC_DEVICE` | None for the system default input |
| `WIDGET_*` | Which monitor and corner the widget sits in |

Times in the greetings use your system clock and time zone.

## Run

```
.venv/bin/python jarvis.py
```

Or as a systemd user service (examples in `systemd/`, they assume the code lives in `~/jarvis`):

```
cp systemd/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now jarvis jarvis-widget
journalctl --user -u jarvis -f       # watch what it hears and does
```

Restart after changing the config: `systemctl --user restart jarvis`.

Dashboard: **http://127.0.0.1:8765** (this PC only). It shows what Jarvis is doing, background jobs, history with every step, what it heard, and has Yes/No buttons and a box for typed commands.

## Controls

| Do | How |
|---|---|
| Talk | "Hey Jarvis", or `jarvisctl listen` |
| Interrupt / cancel | Talk over it, say "Hey Jarvis", or `jarvisctl stop` |
| Type instead of talk | `~/jarvis/jarvisctl say open spotify` |
| Answer a "Shall I...?" | Say yes or no, or `jarvisctl yes` / `jarvisctl no` |
| Voice test | `~/jarvis/jarvisctl speak hello` |
| Status | `~/jarvis/jarvisctl status` |
| Fresh session | Say "new session" |

Hotkeys: in System Settings > Keyboard > Shortcuts, add a new command shortcut for `jarvisctl listen` (say Meta+J) and one for `jarvisctl stop` (say Meta+Shift+J), using the full path to `jarvisctl`.

After you answer, it listens for 5 seconds so you can follow up without the wake word.

## Sessions and notes

Each conversation is a fresh Claude session. A new one starts after 30 minutes idle (`SESSION_IDLE_RESET_MIN`), or when you say "new session". Before a session closes it writes a running summary into `notes.md` (open threads, a dated log of what you asked, lessons about working with you), and the next session reads it first. `notes.md` is created on first use (see `notes.example.md`), stays out of git, and you can edit it by hand.

## Customising the persona

`persona.md` is the system prompt. `$USER_NAME`, `$HONORIFIC`, `$CITY` and `$LINES_FILE` are filled in from the config. Change anything you like. The speaking style is built from real JARVIS lines, collected by situation in `lines.md`. Every message gets a time tag (`[Mon 28 Sep, 07:42, first today]`), so it greets you on your first message of the day, says welcome back after a few hours away, and notices when it is past midnight.

## Safety gate

The gate is `policy` in `brain.py`.

- Allowed without asking: reading, searching, the desktop tools (except Enter), Chrome browsing and clicking, safe Bash, file edits inside your home folder and `WRITE_OK_DIRS`.
- Asks out loud first, with a spoken yes/no: deleting or moving files, sudo, systemctl, git push, POST requests, sending email, anything else that writes somewhere outside, and pressing Enter (that is how most apps send).
- Clicks are not gated, so the persona tells it to ask before clicking Send, Post, Buy or Delete.
- No answer counts as no.

Read `policy` before you trust it with anything important, and add your own rules there.

## Voice

Kokoro works out of the box with British voices (`bm_lewis` is the default). If you want a closer JARVIS sound, you can train your own Piper voice (see the Piper project's training guide), then set `VOICE_ENGINE = "piper"` and `PIPER_MODEL` to the `.onnx` file with its `.onnx.json` next to it. No trained voice or film audio ships with this repo.

## Tests

```
.venv/bin/python test_time_tag.py    # the greeting time tags
.venv/bin/python test_voice.py "Good evening."   # writes logs/voice-kokoro.wav (and piper if set)
```

## Licence

MIT, see `LICENSE`.
