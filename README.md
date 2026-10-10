# talking-board

Talking Board is a small Windows tool that reads text aloud in an AI-generated voice. Copy text and press a hotkey, or let Claude Code send its replies; the text is queued and read in order. Speech is generated on your own machine by the [EMA Lightning](https://huggingface.co/canberkkkkkk/ema-lightning) model (Turkish, single voice, Apache 2.0), so the text you queue should be Turkish. No text or audio leaves the computer; internet is only needed to install the packages and to download the model weights on first start (see [Network access and privacy](#network-access-and-privacy)).

## Requirements

- Windows 10 or 11 (hotkeys and the bubble window use Win32 APIs). The icons use Segoe Fluent Icons, which ships with Windows 11; where it is missing (Windows 10 by default) the app uses Segoe MDL2 Assets instead. Other missing fonts fall back to Tk's defaults.
- Python 3.13 (3.11+ should work; only 3.13 is tested)
- An NVIDIA GPU is recommended; the model also runs on the CPU, more slowly

See [Installation](#installation) for setup.

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
   | Red, "!" | Error: the model failed to load, or reading stopped (the panel shows the reason) |

2. Copy text and press **Alt+Shift+1**. A green ring that grows outward means it was queued; a red ring with a shake means the clipboard was empty, the text is already queued or it is over a [limit](#limits). The badge shows how many items are waiting.
3. Click the bubble to open the queue panel. Each row shows where the text came from (clipboard or the agent's name) and when; the item being read is highlighted. The ✕ on a row removes it; removing the current item stops it immediately, also while paused. The panel also has Pause/Resume, Add from clipboard and Clear all. Esc closes it.
4. **History.** Below the queue, the panel lists the last 10 texts that were read, newest first, with the time reading ended, the source and a mark: **DONE** (read to the end) or **SKIPPED** (removed or cleared while it was being read). Texts removed before they were read do not appear. Each row has three buttons: **replay** (two circling arrows) queues the text again (refused, with the red flash and the reason in the panel, if it is already queued or the queue is full), **copy** puts it on the clipboard and ✕ removes it from the history. **Clear history** empties it; click the **HISTORY** heading to fold the section away. The history is kept in `%APPDATA%\talking-board\history.json` (see [Network access and privacy](#network-access-and-privacy)), so it survives a restart.
5. **Full text.** Click a queue or history row (or its chevron) to expand it and see the whole text; click it again to fold it. Long texts get their own scroll area inside the row. You can select part of the text with the mouse and copy it with Ctrl+C. Expanded rows stay expanded, and the list keeps its scroll position, while the queue changes.
6. **Reading speed.** While a text is being read or reading is paused, turn the mouse wheel over the bubble: each notch makes it 0.05× faster (up) or slower (down), from 0.5× to 2×, and the new value shows briefly above the bubble ("SPEED 1.15×"). The change is heard within a moment, from the word being spoken. When nothing is being read, the wheel over the bubble only shows the current speed. The panel header shows the speed too: the wheel over it changes the speed at any time, and a click sets it back to 1.00×. The speed is saved to `voice.speed` in `config.json` a second after the last change; other settings are left alone, and an open Settings window follows the new value.
7. Drag the bubble to move it; its position is remembered. Right-click it for the menu, including Quit.
8. **Settings…** in the right-click menu, or ⚙ in the panel, opens the voice settings. Changes are heard right away (speed and volume while reading, voice colour and device from the next text). **Try** reads a sample sentence without queueing it, **Save** writes only the `voice` section to `config.json`.

Elsewhere in the panel the wheel scrolls the list. Over an expanded text it scrolls that text first, and the list once the text is at its top or bottom.

If reading fails, nothing is lost: the item stays at the top of the queue, reading pauses, the bubble turns red and the panel shows the reason, for example "Output device 'Headphones' not found — reading paused" or "Speech error: …". Fix the cause (plug the device back in, pick another one in Settings) and press **Resume** to try the item again, or remove it.

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
git clone https://github.com/emreozhan/talking-board.git
cd talking-board
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`requirements.txt` pins the tested versions: the CUDA 12.8 build of torch (for NVIDIA GPUs), EMA Lightning, sounddevice, NumPy and Pillow, which draws the anti-aliased bubble and the icon. The model weights (~34 MB) are downloaded from Hugging Face on first start. Only the very first start takes long (~40 s warm-up); later starts take a few seconds.

`assets/icon.ico` is generated from code; rebuild it after changing the palette with `python -m ema_tts.art`.

## Network access and privacy

- **Speech is generated locally.** Queued text goes to the model on this machine and the audio goes straight to the output device. No text or audio is sent to any service, and no audio is written to disk.
- **The queue lives in memory; the history is a file.** Texts waiting in the queue are not written anywhere. The one exception to "no text on disk" is the history: the last 10 texts that were read (text, source, DONE/SKIPPED and times) are kept in `%APPDATA%\talking-board\history.json` (`~/.talking-board/history.json` if `APPDATA` is not set) so the panel can show them after a restart. **Clear history** in the panel empties the file, and ✕ on a history row removes that text from it. `state.json` holds only the bubble position. The log records item numbers, character counts and the agent's `source` label, never the text. Error output can contain diagnostic details, so look at the log before sharing it.
- **Network use.** `pip install` downloads the packages, and the first start downloads the model weights from Hugging Face (`canberkkkkkk/ema-lightning`). Left alone, the `ema-lightning` package would also contact the Hub on every start: a metadata request per file, plus a `config.json` request that the model author uses as a download counter. No text or audio is part of these requests, but they reveal your IP address. So once the weights are in the Hugging Face cache, the app sets `HF_HUB_OFFLINE=1` for its own process before the Hub library loads, and does not contact the Hub at all. The cache location follows `HF_HUB_CACHE` / `HF_HOME` as usual.
- **Opting out.** Set `EMA_TTS_ONLINE=1` to keep the Hub's default behaviour, for example to pick up a new model revision. An `HF_HUB_OFFLINE` you set yourself is always left as it is.

  ```powershell
  $env:EMA_TTS_ONLINE = "1"
  .\.venv\Scripts\python.exe -m ema_tts
  ```

This describes Talking Board itself, not the network use of Claude Code or other tools that send it text.

## Settings

The `voice` section is edited in the Settings window. To edit by hand, copy `config.example.json` to `config.json`:

| Field | Description |
| --- | --- |
| `hotkeys.add_clipboard` | Queues the clipboard text, default `<alt>+<shift>+1` |
| `hotkeys.pause_resume` | Pauses / resumes, default `<alt>+<shift>+2` |
| `hotkeys.skip` | Skips the current item, default `<alt>+<shift>+3` |
| `hotkeys.toggle_panel` | Opens / closes the panel, default `<alt>+<shift>+4` |
| `hotkeys.clear` | Clears the queue, default `<alt>+<shift>+5` |
| `voice.speed` | Reading speed. 0.5–2 in the window and with the mouse wheel (0.05 steps), 0.25–4 in the file |
| `voice.volume` | Volume, 0–100 (%). Also changes while speaking |
| `voice.seed` | `null`: random timbre and intonation on every reading. A whole number (0–2147483647): the same text always sounds the same. Presets: Low `16`, Natural `1`, High `23` (shift the pitch by about a semitone) |
| `voice.output_device` | Output device name; `null` is the system default. If the named device is missing or cannot be opened, reading pauses with an error; it never switches to another device, so text meant for headphones does not end up on the speakers |
| `voice.sample_rate` | 48000, 24000 or 16000 (8000 also works in the file) |
| `voice.max_chars` | Longest segment long texts are split into, 50–1000 characters |
| `voice.use_lightning` | Fast path with `torch.compile`; usually off on Windows because Triton is missing. Needs a restart |
| `server.enabled` | Turns the local `/speak` endpoint on or off |
| `server.port` | Endpoint port (only on `127.0.0.1`), 1024–65535, default 47821 |
| `server.auto_read` | Whether agent messages are read; also toggled by "Read agent messages" in the right-click menu |

A value of the wrong type or outside these ranges is ignored and the default is used instead; the panel and the log say which field was wrong. The exceptions are `server.enabled` and `server.auto_read`, which turn off when their value is invalid. If `config.json` is not valid JSON, the app starts with the defaults but with agent reading off, and leaves the file alone: Save and the menu refuse to overwrite it until it is fixed. The file is always written atomically.

Hotkey format: modifiers `<ctrl>`, `<alt>`, `<shift>`, `<win>`; as the key a letter, digit, `<f1>`–`<f24>`, `<space>`, `<insert>`, `<delete>`, `<home>`, `<end>`, `<page_up>`, `<page_down>`, `<pause>`, `<scroll_lock>`, the arrow keys or media keys (`<media_play_pause>` etc.); join parts with `+`, e.g. `<ctrl>+<shift>+<f9>`. An empty string (`""`) disables the action. Avoid Ctrl+Alt combinations: on the Turkish Q layout AltGr counts as Ctrl+Alt and they would block typing @ € { } [ ].

## Reading agent replies automatically

While the app runs it listens on `127.0.0.1:47821`. A Claude Code Stop hook can send each reply there to be queued and read.

```powershell
python hooks/install_claude_hook.py              # adds the Stop hook to ~/.claude/settings.json
python hooks/install_claude_hook.py --uninstall  # removes it
```

What the hook does, exactly:

- **It applies to every project.** `~/.claude/settings.json` holds your user-level Claude Code settings, so the hook runs at the end of every Claude Code reply on this machine, in all projects, not only in this one.
- **It checks first.** It reads this checkout's `config.json`. If `server.enabled` or `server.auto_read` is not `true`, or the file cannot be read, it stops and sends nothing. Without a `config.json` both default to on.
- **It sends the reply text.** It takes `last_assistant_message` from the Stop event. On Claude Code versions without that field it reads the session transcript and joins the assistant text after your last prompt; that can include short notes written between tool calls, while thinking, tool calls and subagent messages are skipped.
- **It sends it only to this app.** The text is POSTed to `127.0.0.1:<port>/speak`, nowhere else. The hook exits quietly when the app is closed or anything fails, so Claude never waits on it. The port comes from `config.json`; the `EMA_TTS_PORT` environment variable overrides it.

To pause agent reading, untick "Read agent messages" in the bubble's right-click menu: from then on the hook sends nothing, and the app ignores anything that still reaches `/speak`. This does not stop the current reading or clear the queue; use Pause and Clear all for that. To remove the hook for good, run the `--uninstall` command above (with the same `--settings` / `--hook` options you installed with, if any).

The installer keeps your other settings and writes `settings.json` atomically; running it again does not duplicate the entry. The first time it changes the file it copies the original to `settings.json.bak-ema-tts`, and later runs never overwrite that copy, so it always holds your settings from before the first install; delete it once you no longer need it. It recognises its own entry by the hook's path: this checkout's `hooks/claude_stop_hook.py`, or `hooks/claude_stop_hook.py` in an earlier checkout folder named `ema-tts` or `talking-board`, so reinstalling after moving the project replaces the old entry. Other hooks, including other tools' scripts with the same file name, are left alone.

Other tools can send text too; `source` (optional) is shown on the queue row:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:47821/speak -ContentType 'application/json' -Body '{"text": "Merhaba", "source": "codex"}'
```

Responses: `202 {"id": n}` queued, `202 {"ignored": true}` reading is off, `409` empty or already queued, `400` invalid JSON, `403` browser or foreign host, `413` body larger than 256 KB or text longer than 20 000 characters, `415` Content-Type is not `application/json`, `429` the queue is full. Error bodies look like `{"error": "...", "field": "text"}`. `source` is cut to 64 characters, and line breaks and other control characters in it become spaces.

### Limits

The endpoint accepts requests from any program on this machine, so it has fixed limits:

- one text: at most 20 000 characters;
- the queue: at most 100 items and 200 000 characters in total;
- connections: at most 16 at a time, and a client that stays silent for 3 seconds while sending its request is disconnected.

Texts over a limit are refused, from the clipboard as well (red flash), and the log says why.

## Responsible use

Talking Board produces synthetic speech with the EMA Lightning voice. It is meant to read text to *you*, on your own speakers or headphones, and it never writes audio to a file. If you take it beyond that, keep the model author's requests in mind (see the [model card](https://huggingface.co/canberkkkkkk/ema-lightning#responsible-use)) and the law where you are:

- **Tell people when they hear it.** If the audio can reach anyone else (a screen share, a meeting, a recording, a stream), say or show that the voice is AI-generated. In Turkish: "Bu ses yapay zekâ ile üretilmiştir."
- **Label anything you publish.** If you record the output or build something that saves it, mark it as AI-generated, in the audio and in the metadata. In the EU, Article 50 of the AI Act requires providers of systems that generate synthetic audio to mark it in a machine-readable way; this app adds no such mark because it never produces a file.
- **No impersonation, no deception.** Do not present the voice as a real person's, do not use it in scams or calls that pretend to be human, and do not make it say things a real person did not say.
- **Check sensitive text.** Numbers, dates and abbreviations go through automatic normalization and can be misread. Do not rely on the output alone for banking, health, legal or emergency messages.
- **Mind what you queue.** Clipboard text and agent replies may contain other people's personal data or secrets. Everything stays on this machine, but the speakers and the queue panel are visible to whoever is next to you or on your call, and the last 10 texts read stay in `history.json` in your user profile until you clear the history. If you choose headphones as the output device and they disappear, reading pauses instead of moving to the speakers; with "System default", Windows decides where the sound goes.

The voice was trained by the model author on what the model card calls "an internal corpus of about 1000 hours of single-speaker Turkish speech". The card does not say whose voice it is or how consent was obtained, and this project has no further information. If that matters for your use, ask the model author before relying on it.

## Design

"Night broadcast": deep navy surfaces with one saffron signal colour, like an on-air lamp that stays quiet until it has something to say. Bahnschrift for titles and labels, Segoe UI Variable for reading text, Cascadia Mono for keys and numbers, Segoe Fluent Icons for glyphs. The queue panel carries a small "AI VOICE" tag and the settings window says "AI-generated speech · EMA Lightning", so the voice is never mistaken for a recording of a person. All colours, fonts, radii and spacing live in `ema_tts/theme.py`; the bubble and other anti-aliased shapes are drawn in `ema_tts/art.py`.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

## License

Copyright 2026 EmreOzhan. Licensed under the [Apache License 2.0](LICENSE).

## Third-party notices

This repository contains only its own code and a generated icon. Everything below is downloaded by `pip` or by the `ema-lightning` package and is **not** redistributed here; the licenses are listed so you know what you are installing. Talking Board is not affiliated with or endorsed by these projects, and its Apache-2.0 license does not relicense them.

| Component | Author / project | License | Notes |
| --- | --- | --- | --- |
| [EMA Lightning](https://huggingface.co/canberkkkkkk/ema-lightning) (weights and the `ema-lightning` package, [source](https://github.com/canberk7/ema-lightning)) | Canberk Aslan | Apache-2.0 | The weights (~34 MB) are fetched from Hugging Face on first start. Citation: Aslan, C. (2026), *EMA Lightning: Tiny, Fast and Accurate Turkish Text to Speech*. |
| [normalizer-tr](https://github.com/erdemtuna/normalizer-tr) | Erdem Tuna | Apache-2.0 | Turkish text normalizer used by EMA Lightning; bundles Unicode CLDR data under the Unicode license. |
| [PyTorch](https://pytorch.org) | PyTorch contributors | BSD-3-Clause | The `cu128` wheel bundles NVIDIA CUDA/cuDNN and Intel OpenMP libraries under their own license terms. |
| [NumPy](https://numpy.org) | NumPy developers | BSD-3-Clause and others | The Windows wheel bundles OpenBLAS with the GCC runtime (GPL-3.0 with the GCC Runtime Library Exception). |
| [python-sounddevice](https://python-sounddevice.readthedocs.io) | Matthias Geier | MIT | Bundles PortAudio DLLs (PortAudio license), including a build with ASIO support. |
| [Pillow](https://python-pillow.org) | Jeffrey A. Clark and contributors | MIT-CMU | Bundles FreeType, libjpeg-turbo, libpng, zlib-ng and other image libraries; see its license file. |
| [huggingface_hub](https://github.com/huggingface/huggingface_hub) | Hugging Face | Apache-2.0 | Pulled in by `ema-lightning`; used only to download the weights. Pulls in [tqdm](https://github.com/tqdm/tqdm) (MPL-2.0 and MIT). |

Installing these is fine as it is. If you ever bundle the virtual environment or a frozen build into an installer or archive, you redistribute them and their terms apply to you: keep their license files and notices, honour the source-availability terms of the MPL-2.0 (tqdm) and LGPL-3.0 (`autocommand`, vendored inside `setuptools`) parts, and check the NVIDIA CUDA/cuDNN and Intel OpenMP terms for the DLLs in `torch/lib` and the Steinberg ASIO SDK terms for the ASIO build of PortAudio. Take the inventory from the files you actually ship, not from this table.

The UI refers to Microsoft system fonts (Bahnschrift, Segoe UI Variable, Cascadia Mono, Segoe Fluent Icons, Segoe MDL2 Assets) by name. The fonts are not part of this repository, and `assets/icon.ico` is drawn from geometric shapes only; it contains no glyphs from any font.
