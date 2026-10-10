# ema-tts

A small Windows tool that reads text aloud locally, without internet. Copy text and press a hotkey, or let Claude Code send its replies; the text is queued and read in order. The voice model is [EMA Lightning](https://huggingface.co/canberkkkkkk/ema-lightning) (Turkish, single voice, Apache 2.0), so the text you queue should be Turkish.

## Running

From the project folder, without a console window, with logs written to `ema-tts.log`:

```powershell
.\.venv\Scripts\pythonw.exe -c "import sys; sys.stdout = sys.stderr = open('ema-tts.log', 'w', encoding='utf-8', buffering=1); from ema_tts.app import main; main()"
```

The short form `.\.venv\Scripts\pythonw.exe -m ema_tts` also starts the app without a console, but `pythonw` discards `print` output, so errors are lost. To watch the log live, run it with a console instead:

```powershell
.\.venv\Scripts\python.exe -m ema_tts
```

Run only one copy at a time: a second copy shows its own bubble but cannot use the server port or the hotkeys. A local `start.bat` / `start.cmd` with the first command is handy; such scripts are git-ignored.

## Usage

1. Start the app. A round bubble appears near the bottom right of the screen. Its shape shows the state, so it works without relying on colour:

   | Bubble | State |
   | --- | --- |
   | Spinning arc, dim bars | Loading the model |
   | Navy, five resting bars | Ready |
   | Saffron, moving bars, breathing halo | Speaking |
   | Saffron ring, two bars | Paused |
   | Red, "!" | Error (the panel shows the reason) |

2. Copy text and press **Alt+Shift+1**. A green ring that grows outward means it was queued; a red ring with a shake means the clipboard was empty or the text is already queued. The badge shows how many items are waiting.
3. Click the bubble to open the queue panel. Each row shows where the text came from (clipboard or the agent's name) and when; the item being read is highlighted. The ✕ on a row removes it; removing the current item stops it immediately. The panel also has Pause/Resume, Add from clipboard and Clear all. Esc closes it.
4. Drag the bubble to move it; its position is remembered. Right-click it for the menu, including Quit.
5. **Settings…** in the right-click menu, or ⚙ in the panel, opens the voice settings. Changes are heard right away (speed, voice colour and device from the next text; volume immediately). **Try** reads a sample sentence without queueing it, **Save** writes only the `voice` section to `config.json`.

Hotkeys (left Alt + Shift + digit, all reachable with the left hand):

| Hotkey | Action |
| --- | --- |
| Alt+Shift+1 | Queue the clipboard text |
| Alt+Shift+2 | Pause / resume |
| Alt+Shift+3 | Skip the current item (removes it from the queue) |
| Alt+Shift+4 | Open / close the panel |
| Alt+Shift+5 | Clear the queue |

Hotkeys are registered with Windows through `RegisterHotKey`, so they do not clash with other programs. If another program already uses one, the app still starts and the panel and `ema-tts.log` show a warning such as "Alt+Shift+1 is used by another program (hotkeys.add_clipboard)". Pick another hotkey in `config.json` then.

Markdown is cleaned up before reading: headings, bullets and bold/italic marks are dropped, code blocks are read as "Kod bloğu" and URLs as "bağlantı" (Turkish, because that is what the model speaks).

## Installation

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`requirements.txt` pulls the CUDA 12.8 build of torch (for NVIDIA GPUs) and Pillow, which draws the anti-aliased bubble and the icon. The model weights (~34 MB) are downloaded from Hugging Face on first start. Only the very first start takes long (~40 s warm-up); later starts take a few seconds.

`assets/icon.ico` is generated from code; rebuild it after changing the palette with `python -m ema_tts.art`.

## Settings

The `voice` section is edited in the Settings window. To edit by hand, copy `config.example.json` to `config.json`:

| Field | Description |
| --- | --- |
| `hotkeys.add_clipboard` | Queues the clipboard text, default `<alt>+<shift>+1` |
| `hotkeys.pause_resume` | Pauses / resumes, default `<alt>+<shift>+2` |
| `hotkeys.skip` | Skips the current item, default `<alt>+<shift>+3` |
| `hotkeys.toggle_panel` | Opens / closes the panel, default `<alt>+<shift>+4` |
| `hotkeys.clear` | Clears the queue, default `<alt>+<shift>+5` |
| `voice.speed` | Reading speed. 0.5–2 in the window (0.05 steps), 0.25–4 in the file |
| `voice.volume` | Volume, 0–100 (%). Also changes while speaking |
| `voice.seed` | `null`: random timbre and intonation on every reading. A number (0–2147483647): the same text always sounds the same. Presets: Low `16`, Natural `1`, High `23` (shift the pitch by about a semitone) |
| `voice.output_device` | Output device name; `null` is the system default. Falls back to the default if the device is missing |
| `voice.sample_rate` | 48000, 24000 or 16000 (8000 also works in the file) |
| `voice.max_chars` | Longest segment long texts are split into, 50–1000 characters |
| `voice.use_lightning` | Fast path with `torch.compile`; usually off on Windows because Triton is missing. Needs a restart |
| `server.enabled` | Turns the local `/speak` endpoint on or off |
| `server.port` | Endpoint port (only on `127.0.0.1`), default 47821 |
| `server.auto_read` | Whether agent messages are queued; also toggled by "Read agent messages" in the right-click menu |

Hotkey format: modifiers `<ctrl>`, `<alt>`, `<shift>`, `<win>`; as the key a letter, digit, `<f1>`–`<f24>`, `<space>`, `<insert>`, `<delete>`, `<home>`, `<end>`, `<page_up>`, `<page_down>`, `<pause>`, `<scroll_lock>`, the arrow keys or media keys (`<media_play_pause>` etc.); join parts with `+`, e.g. `<ctrl>+<shift>+<f9>`. An empty string (`""`) disables the action. Avoid Ctrl+Alt combinations: on the Turkish Q layout AltGr counts as Ctrl+Alt and they would block typing @ € { } [ ].

## Reading agent replies automatically

While the app runs it listens on `127.0.0.1:47821`. Claude Code's Stop hook sends every reply there; the text is queued and read.

```powershell
python hooks/install_claude_hook.py              # adds the Stop hook to ~/.claude/settings.json
python hooks/install_claude_hook.py --uninstall  # removes it
```

The installer keeps your existing settings and writes a `settings.json.bak-ema-tts` backup first; running it again does not duplicate the entry. The hook exits quietly when the app is closed or anything fails, so Claude never waits on it. To pause reading for a while, untick "Read agent messages" in the bubble's right-click menu. The hook reads the port from `config.json`; the `EMA_TTS_PORT` environment variable overrides it.

Other tools can send text too; `source` (optional) is shown on the queue row:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:47821/speak -ContentType 'application/json' -Body '{"text": "Merhaba", "source": "codex"}'
```

Responses: `202 {"id": n}` queued, `202 {"ignored": true}` reading is off, `409` empty or already queued, `400` invalid JSON, `403` browser or foreign host, `413` body larger than 256 KB, `415` Content-Type is not `application/json`. Error bodies look like `{"error": "...", "field": "text"}`.

## Design

"Night broadcast": deep navy surfaces with one saffron signal colour, like an on-air lamp that stays quiet until it has something to say. Bahnschrift for titles and labels, Segoe UI Variable for reading text, Cascadia Mono for keys and numbers, Segoe Fluent Icons for glyphs. All colours, fonts, radii and spacing live in `ema_tts/theme.py`; the bubble and other anti-aliased shapes are drawn in `ema_tts/art.py`.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```
