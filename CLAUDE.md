# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
python setup.py     # installs requirements + playwright browsers (one-time)
python main.py      # run the assistant
```

No tests, no linter, no build step. Verification is manual: run `main.py` and watch stdout — every subsystem logs with a tagged prefix (`[JARVIS]`, `[Vision]`, `[Memory]`, `[Dashboard]`, `[Weather]`, …). Python 3.11+ required (`asyncio.TaskGroup`, `BaseExceptionGroup`).

## Architecture

One Gemini Live session is the whole brain. There is no local orchestration loop, no intent classifier, no router — audio streams to `models/gemini-2.5-flash-native-audio-preview-12-2025` and the model decides which of ~23 declared tools to call.

**Two-thread process** (`main.py:1521`): Qt owns the main thread (`ui.root.mainloop()` → `QApplication.exec()`); everything else runs in an asyncio loop on a daemon thread. `JarvisUI` (`ui.py:3251`) is a thin thread-safe façade over `MainWindow` — every method emits a Qt signal. Background code must only touch the UI through that façade (`write_log`, `set_state`, `show_content`, `start_camera_stream`, …); never reach into `ui._win`.

**`JarvisLive.run()` (`main.py:1400`)** is an infinite reconnect loop. Each iteration rebuilds the config, makes a *fresh* `genai.Client`, and opens a `TaskGroup` of long-lived tasks: mic capture, realtime send, receive, playback, system monitor, background monitor, proactive engine, phone-audio relay. It catches `BaseException` deliberately — `TaskGroup` raises `BaseExceptionGroup`, which `except Exception` would let escape and kill `asyncio.run()`. Backoff is exponential on network errors; an invalid API key parks the loop on the setup overlay instead of retrying.

**System prompt is assembled per session** (`_build_config`, `main.py:649`): current date/time + identity block (assistant/user name from config, overriding anything in `prompt.txt`) + formatted long-term memory + `core/prompt.txt`. Changing tool-selection *behaviour* usually means editing the TOOL ROUTING section of `core/prompt.txt`, not the Python.

### Tool dispatch

Adding or changing a tool means touching three places:

1. `TOOL_DECLARATIONS` (`main.py:100`) — the Gemini function declaration.
2. A branch in `_execute_tool` (`main.py:706`) — dispatch.
3. `core/prompt.txt` TOOL ROUTING — so the model knows when to pick it.

Action modules are plain **synchronous** functions, always called through `loop.run_in_executor` so they never block the audio loop:

```python
r = await loop.run_in_executor(None, lambda: open_app(parameters=args, response=None, player=self.ui))
result = r or "Opened."
```

Convention: `parameters: dict` in, human-readable string out, `player=` is the UI (used for `write_log`), and the returned string is sent back as the `FunctionResponse` — so it is what the model speaks from. Exceptions are caught centrally in `_execute_tool` and spoken via `speak_error`. Special response shapes: `{"result": ..., "silent": True}` suppresses speech (see `save_memory`).

**Vision is two-phase.** `screen_process` does *not* return the image. It captures, stashes it in `self._pending_vision`, and returns a `[VISION_ACTIVE]` instruction telling the model to say one short filler sentence. The image is injected as the *next* message from the receive loop. Guarded by `_vision_busy` + a 4s `_vision_last_time` cooldown, because speaker echo used to trigger duplicate calls. `_vision_cam_active` / `_vision_close_pending` auto-close the webcam after the analysis turn.

### Memory

`memory/long_term.json` (untracked, created on first save) with fixed categories: identity, preferences, projects, relationships, wishes, notes — plus a `sessions` list. Hard budget: `MEMORY_MAX_CHARS = 2200`, oldest-`updated` entries trimmed first; values truncated at 380 chars. The cap exists because the whole store is injected into every system prompt.

Session summaries are **consume-once**: `save_session_summary` writes on disconnect (only if ≥3 turns), `pop_last_session` reads *and deletes* so the morning briefing never repeats itself.

### Dashboard

`dashboard/server.py` — optional FastAPI + uvicorn on port `8000`, LAN phone control paired by QR/key, with AES-encrypted payloads and best-effort OS firewall punching. Import failure is swallowed (`main.py:1404`) and `self._dashboard` stays `None`; every use site must handle that. Phone mic streaming sets `_phone_active`, which pauses the PC mic.

### Dead code

`core/llm_client.py`, `core/tts.py`, `core/stt.py`, `core/installer.py` are **not imported by anything** — leftovers from an earlier local-Ollama/local-TTS architecture. Don't wire them back in or "fix" them as part of unrelated work.

## Gotchas

- **There is no `.gitignore`.** `config/api_keys.json` holds the Gemini key and `memory/long_term.json` holds personal data. Neither is tracked today — keep it that way; add them to a `.gitignore` before any commit that could sweep them in.
- `main.py` passes `"face.png"`, which is not in the repo; `_load_face` swallows the failure and the HUD runs without it.
- `requirements.txt` is deliberately incomplete for OS-specific extras — a `ModuleNotFoundError` at runtime is expected behaviour, install the named package.
- Turkish and English are both first-class: user-facing strings, prompt rules, and even some log lines are bilingual. Preserve the language-mirroring behaviour (respond in the user's language; `sir` / `efendim` never mixed).
- Crypto/financial/trading topics are blocked at code level in `actions/background_monitor.py` regardless of what the user asks. Intentional.
