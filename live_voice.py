#!/home/ilham/Projects/Ciel_V3/venv/bin/python
import asyncio
import collections
import datetime
import json
import os
import pathlib
import re
import shutil
import subprocess
import threading
import traceback
import urllib.parse
import numpy as np
import sounddevice as sd

VOICE_LOG = "/tmp/ciel_voice.log"
ENV_PATH = pathlib.Path("/home/ilham/Projects/Ciel_V3/.env")

LIVE_MODELS = [
    "gemini-2.5-flash-native-audio-preview-12-2025",
    "gemini-2.5-flash-native-audio-preview-09-2025",
    "gemini-3.1-flash-live-preview",
]


def vlog(msg: str):
    try:
        with open(VOICE_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now().isoformat(timespec='seconds')}] {msg}\n")
    except Exception:
        pass


def _load_gemini_api_key() -> str:
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        val = os.environ.get(k, "").strip()
        if val:
            return val
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            if k in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
                val = v.strip().strip('"').strip("'")
                if val:
                    os.environ[k] = val
                    return val
    try:
        import config  # type: ignore
        for attr in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
            val = getattr(config, attr, "")
            if isinstance(val, str) and val.strip():
                return val.strip()
    except Exception:
        pass
    return ""


def clean_for_speech(text: str) -> str:
    text = re.sub(r"```.*?```", " I've placed the code block on your screen. ", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    text = re.sub(r"[#*_~>|]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _spawn_detached(cmd: list[str]) -> str:
    subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return "launched"


def _tool_open_url(url: str) -> str:
    target = (url or "").strip()
    if not target:
        return "Error: empty URL"
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", target):
        target = "https://" + target
    opener = shutil.which("xdg-open") or shutil.which("kde-open") or shutil.which("firefox") or shutil.which("chromium")
    if not opener:
        return "Error: no browser or xdg-open found on PATH"
    _spawn_detached([opener, target])
    return f"Opened {target} in the default browser."


def _tool_search_youtube(query: str = "") -> str:
    q = (query or "").strip()
    if not q:
        return _tool_open_url("https://www.youtube.com")
    encoded = urllib.parse.quote_plus(q)
    return _tool_open_url(f"https://www.youtube.com/results?search_query={encoded}")


def _tool_open_app(app_name: str) -> str:
    name = (app_name or "").strip().lower()
    if not name:
        return "Error: empty app_name"
    aliases = {
        "browser": ["firefox", "google-chrome-stable", "chromium", "brave", "zen-browser"],
        "web browser": ["firefox", "google-chrome-stable", "chromium", "brave", "zen-browser"],
        "youtube": ["__YOUTUBE__"],
        "terminal": ["konsole", "kitty", "alacritty"],
        "konsole": ["konsole"],
        "files": ["dolphin"],
        "file manager": ["dolphin"],
        "dolphin": ["dolphin"],
        "vscode": ["code"],
        "vs code": ["code"],
        "code": ["code"],
        "settings": ["systemsettings"],
        "system settings": ["systemsettings"],
    }
    candidates = aliases.get(name, [name])
    if candidates == ["__YOUTUBE__"]:
        return _tool_open_url("https://www.youtube.com")
    for bin_name in candidates:
        path = shutil.which(bin_name)
        if path:
            _spawn_detached([path])
            return f"Launched {bin_name}."
    return f"Could not find executable for '{app_name}'."


def _build_live_tools_and_registry():
    from google.genai import types

    registry = {
        "open_url": lambda url="": _tool_open_url(url),
        "search_youtube": lambda query="": _tool_search_youtube(query),
        "open_app": lambda app_name="": _tool_open_app(app_name),
    }

    declarations = [
        types.FunctionDeclaration(
            name="open_url",
            description="Open a website or URL in the user's default web browser (e.g. https://www.youtube.com, https://github.com).",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "url": types.Schema(type=types.Type.STRING, description="The URL or domain to open."),
                },
                required=["url"],
            ),
        ),
        types.FunctionDeclaration(
            name="search_youtube",
            description="Open YouTube in the browser, optionally searching for a specific video or topic.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(type=types.Type.STRING, description="Optional search query for YouTube; leave empty to open YouTube home."),
                },
            ),
        ),
        types.FunctionDeclaration(
            name="open_app",
            description="Launch a desktop application on Arch Linux KDE Plasma (e.g. browser, terminal, konsole, dolphin, code, system settings).",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "app_name": types.Schema(type=types.Type.STRING, description="Name of the application to launch."),
                },
                required=["app_name"],
            ),
        ),
    ]

    # Also bridge safe Ciel engine tools if available
    try:
        import engine  # type: ignore
        emap = getattr(engine, "tool_map", {}) or {}
        eschemas = getattr(engine, "tool_schemas", []) or []
        for schema in eschemas:
            fn_info = schema.get("function", schema) if isinstance(schema, dict) else {}
            fname = fn_info.get("name", "")
            if not fname or fname in registry or fname not in emap:
                continue
            params = fn_info.get("parameters") or {"type": "object", "properties": {}}
            declarations.append(
                types.FunctionDeclaration(
                    name=fname,
                    description=fn_info.get("description", f"Run Ciel tool {fname}"),
                    parameters=params,
                )
            )
            registry[fname] = emap[fname]
    except Exception as exc:
        vlog(f"Optional engine tool bridge skipped: {exc}")

    return [types.Tool(function_declarations=declarations)], registry


class LiveVoiceEngine:
    def __init__(self, on_transcript, on_state=None, is_busy=None, on_level=None):
        self.on_transcript = on_transcript
        self.on_state = on_state or (lambda _s: None)
        self.is_busy = is_busy or (lambda: False)
        self.on_level = on_level or (lambda _lvl: None)

        self.in_rate = 16000
        self.out_rate = 24000
        self.running = False
        self.speaking = False
        self._server_generating = False
        self._thread = None
        self._loop = None
        self._play_buf = collections.deque()
        self._play_lock = threading.Lock()
        self._user_buf = []
        self._ciel_buf = []
        self._seen_call_ids = collections.deque(maxlen=64)

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run_thread, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        self.interrupt_speech()
        self.on_state("IDLE")
        vlog("Gemini LiveVoiceEngine stopped")

    def interrupt_speech(self):
        with self._play_lock:
            self._play_buf.clear()
        self._server_generating = False
        self.speaking = False

    def speak(self, raw_text: str, on_done=None):
        spoken = clean_for_speech(raw_text)
        if not spoken:
            if on_done:
                on_done()
            return
        api_key = _load_gemini_api_key()
        if not api_key:
            if on_done:
                on_done()
            return

        def _one_shot():
            try:
                asyncio.run(self._speak_once_async(api_key, spoken))
            except Exception as exc:
                vlog(f"One-shot Live speak error: {exc}")
            finally:
                self.speaking = False
                self.on_state("LISTENING" if self.running else "IDLE")
                if on_done:
                    on_done()

        threading.Thread(target=_one_shot, daemon=True).start()

    async def _speak_once_async(self, api_key: str, text: str):
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})
        cfg = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Aoede")
                )
            ),
            system_instruction="Read the user's message aloud naturally and concisely without adding commentary.",
        )
        self.speaking = True
        self.on_state("SPEAKING")
        with sd.OutputStream(samplerate=self.out_rate, channels=1, dtype="int16", blocksize=1024) as out_stream:
            async with client.aio.live.connect(model=LIVE_MODELS[0], config=cfg) as session:
                await session.send(input=text, end_of_turn=True)
                async for msg in session.receive():
                    if not self.speaking:
                        break
                    sc = getattr(msg, "server_content", None)
                    if sc and getattr(sc, "model_turn", None):
                        for part in sc.model_turn.parts or []:
                            inline = getattr(part, "inline_data", None)
                            if inline and getattr(inline, "data", None):
                                pcm = np.frombuffer(inline.data, dtype=np.int16)
                                if pcm.size:
                                    rms = float(np.sqrt(np.mean(np.square(pcm.astype(np.float32) / 32768.0))))
                                    self.on_level(rms)
                                    out_stream.write(pcm)
                    if sc and getattr(sc, "turn_complete", False):
                        break

    def _run_thread(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._live_session_loop())
        except Exception as exc:
            vlog(f"Gemini Live loop fatal error: {exc}\n{traceback.format_exc()}")
            self.on_state(f"MIC_ERR: {exc}")
            self.running = False
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    def _flush_user_transcript(self):
        txt = "".join(self._user_buf).strip()
        self._user_buf.clear()
        if txt:
            vlog(f"User (Live): {txt}")
            self.on_transcript(f"__LIVE_USER__:{txt}")
            if re.search(r"\b(stop listening|exit voice|mute yourself|goodbye ciel)\b", txt, re.I):
                self.stop()

    def _flush_ciel_transcript(self):
        txt = "".join(self._ciel_buf).strip()
        self._ciel_buf.clear()
        if txt:
            vlog(f"CIEL (Live): {txt}")
            self.on_transcript(f"__LIVE_CIEL__:{txt}")

    async def _live_session_loop(self):
        api_key = _load_gemini_api_key()
        if not api_key:
            vlog("Missing GEMINI_API_KEY / GOOGLE_API_KEY")
            self.on_state("MIC_ERR: Set GEMINI_API_KEY in .env")
            self.running = False
            return

        from google import genai
        from google.genai import types

        self.on_state("LOADING_STT")
        tools, tool_registry = _build_live_tools_and_registry()
        vlog(f"Loaded {len(tool_registry)} Live tools: {list(tool_registry.keys())}")

        client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})

        cfg = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Aoede")
                )
            ),
            tools=tools,
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            context_window_compression=types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow()
            ),
            system_instruction=(
                "You are CIEL, a fast, sharp desktop AI assistant on Arch Linux KDE Plasma. "
                "When the user asks to open a browser, open YouTube, search YouTube, open an app, or run a desktop action, "
                "immediately call the matching tool (open_url, search_youtube, open_app, or Ciel system tools) and confirm briefly."
            ),
        )

        last_err = None
        for model_name in LIVE_MODELS:
            if not self.running:
                return
            try:
                vlog(f"Connecting Gemini Live API ({model_name})...")
                async with client.aio.live.connect(model=model_name, config=cfg) as session:
                    vlog(f"Connected to Gemini Live ({model_name})")
                    self.on_state("LISTENING")
                    await self._stream_bidirectional(session, tool_registry)
                    return
            except Exception as exc:
                last_err = exc
                vlog(f"Model {model_name} failed ({exc})")

        self.on_state(f"MIC_ERR: {last_err}")
        self.running = False

    async def _execute_tool_calls(self, session, tool_call, tool_registry: dict):
        from google.genai import types

        responses = []
        for fc in getattr(tool_call, "function_calls", None) or []:
            call_id = getattr(fc, "id", None)
            fname = getattr(fc, "name", "")
            fargs = dict(getattr(fc, "args", None) or {})
            if call_id and call_id in self._seen_call_ids:
                vlog(f"Skipping duplicate tool_call id={call_id} ({fname})")
                continue
            if call_id:
                self._seen_call_ids.append(call_id)

            vlog(f"Executing Live tool: {fname}({fargs})")
            fn = tool_registry.get(fname)
            if not fn:
                out = f"Error: unknown tool '{fname}'"
            else:
                try:
                    res = await asyncio.to_thread(fn, **fargs)
                    out = res if isinstance(res, str) else json.dumps(res, ensure_ascii=False, default=str)
                except Exception as exc:
                    out = f"Tool error in {fname}: {exc}"
            vlog(f"Tool result ({fname}): {out[:200]}")
            responses.append(
                types.FunctionResponse(
                    id=call_id,
                    name=fname,
                    response={"result": out},
                )
            )
        if responses:
            await session.send_tool_response(function_responses=responses)

    async def _stream_bidirectional(self, session, tool_registry: dict):
        from google.genai import types

        audio_in_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=64)
        loop = asyncio.get_running_loop()
        barge_hits = 0

        def _mic_callback(indata, frames, _time_info, status):
            nonlocal barge_hits
            if not self.running:
                return
            raw_arr = np.frombuffer(bytes(indata), dtype=np.int16)
            norm = raw_arr.astype(np.float32) / 32768.0
            rms = float(np.sqrt(np.mean(np.square(norm)))) if norm.size else 0.0

            if self.speaking or self._server_generating:
                if rms > 0.075:
                    barge_hits += 1
                else:
                    barge_hits = max(0, barge_hits - 1)
                if barge_hits < 3:
                    pcm_bytes = bytes(len(raw_arr) * 2)
                else:
                    pcm_bytes = bytes(indata)
            else:
                barge_hits = 0
                pcm_bytes = bytes(indata)
                try:
                    self.on_level(rms)
                except Exception:
                    pass

            try:
                if audio_in_q.full():
                    audio_in_q.get_nowait()
                loop.call_soon_threadsafe(audio_in_q.put_nowait, pcm_bytes)
            except Exception:
                pass

        def _speaker_callback(outdata, frames, _time_info, status):
            needed = frames
            out = np.zeros(needed, dtype=np.int16)
            filled = 0
            buf_empty = False
            with self._play_lock:
                while filled < needed and self._play_buf:
                    chunk = self._play_buf[0]
                    take = min(needed - filled, chunk.size)
                    out[filled:filled + take] = chunk[:take]
                    if take == chunk.size:
                        self._play_buf.popleft()
                    else:
                        self._play_buf[0] = chunk[take:]
                    filled += take
                buf_empty = len(self._play_buf) == 0
            outdata[:, 0] = out
            if filled > 0:
                rms = float(np.sqrt(np.mean(np.square(out[:filled].astype(np.float32) / 32768.0))))
                try:
                    self.on_level(rms)
                except Exception:
                    pass
            elif self.speaking and buf_empty and not self._server_generating:
                self.speaking = False
                if self.running:
                    self.on_state("LISTENING")

        async def _send_mic():
            while self.running:
                pcm_bytes = await audio_in_q.get()
                if not self.running:
                    break
                await session.send_realtime_input(
                    audio=types.Blob(data=pcm_bytes, mime_type=f"audio/pcm;rate={self.in_rate}")
                )

        async def _recv_server():
            while self.running:
                async for msg in session.receive():
                    if not self.running:
                        return

                    tc = getattr(msg, "tool_call", None)
                    if tc:
                        self._flush_user_transcript()
                        await self._execute_tool_calls(session, tc, tool_registry)
                        continue

                    sc = getattr(msg, "server_content", None)
                    if not sc:
                        continue

                    if getattr(sc, "interrupted", False):
                        vlog("Server turn interrupted")
                        self.interrupt_speech()
                        self._flush_ciel_transcript()
                        if self.running:
                            self.on_state("LISTENING")
                        continue

                    in_tr = getattr(sc, "input_transcription", None)
                    if in_tr and getattr(in_tr, "text", None):
                        self._user_buf.append(in_tr.text)

                    out_tr = getattr(sc, "output_transcription", None)
                    if out_tr and getattr(out_tr, "text", None):
                        if self._user_buf:
                            self._flush_user_transcript()
                        self._ciel_buf.append(out_tr.text)

                    mt = getattr(sc, "model_turn", None)
                    if mt and getattr(mt, "parts", None):
                        self._server_generating = True
                        if self._user_buf:
                            self._flush_user_transcript()
                        for part in mt.parts:
                            inline = getattr(part, "inline_data", None)
                            if inline and getattr(inline, "data", None):
                                pcm = np.frombuffer(inline.data, dtype=np.int16).copy()
                                if pcm.size:
                                    with self._play_lock:
                                        self._play_buf.append(pcm)
                                    if not self.speaking:
                                        self.speaking = True
                                        self.on_state("SPEAKING")

                    if getattr(sc, "generation_complete", False) or getattr(sc, "turn_complete", False):
                        self._server_generating = False
                        self._flush_user_transcript()
                        self._flush_ciel_transcript()

        with sd.InputStream(
            samplerate=self.in_rate,
            channels=1,
            dtype="int16",
            blocksize=1024,
            callback=_mic_callback,
        ), sd.OutputStream(
            samplerate=self.out_rate,
            channels=1,
            dtype="int16",
            blocksize=1024,
            callback=_speaker_callback,
        ):
            sender = asyncio.create_task(_send_mic())
            receiver = asyncio.create_task(_recv_server())
            try:
                done, pending = await asyncio.wait(
                    [sender, receiver], return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                for task in done:
                    exc = task.exception()
                    if exc:
                        raise exc
            finally:
                self.interrupt_speech()
