import asyncio
import json
import os
import sys
from dotenv import load_dotenv
from google import genai
from google.genai import types
import sounddevice as sd

BASE_DIR = "/home/ilham/Projects/Ciel_V3"
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

load_dotenv(os.path.join(BASE_DIR, ".env"))

from tools import tool_map, tool_schemas
from tools.system import mpv_control

VOICE_ALLOWED_TOOLS = {
    "mpv_control",
    "system_control",
    "get_desktop_context",
    "repo_map",
    "read_file",
    "run_terminal",
}


def _build_voice_system_prompt() -> str:
    prompt = (
        "You are CIEL, Ilham's personal AI assistant running on his Arch Linux laptop (/home/ilham/Projects/Ciel_V3). "
        "Keep spoken responses concise, natural, and direct (1 to 3 sentences unless asked for depth). "
        "Never read out raw file paths, markdown symbols, or long tables aloud—summarize clearly. "
        "You have live tools to control background mpv music, adjust system volume/brightness, "
        "inspect active window/clipboard context, and inspect or run commands in his workspace."
    )
    ciel_md = os.path.join(BASE_DIR, "CIEL.md")
    if os.path.isfile(ciel_md):
        try:
            with open(ciel_md, "r", encoding="utf-8") as f:
                prompt += "\n\n" + f.read().strip()[:1500]
        except Exception:
            pass
    return prompt


def _build_live_function_declarations() -> list[dict]:
    decls = []
    for item in tool_schemas:
        fn = item.get("function") or {}
        name = fn.get("name", "")
        if name not in VOICE_ALLOWED_TOOLS:
            continue
        params = fn.get("parameters") or {"type": "object", "properties": {}}
        decls.append(
            {
                "name": name,
                "description": fn.get("description", ""),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        k: {
                            "type": str(v.get("type", "string")).upper(),
                            "description": v.get("description", ""),
                        }
                        for k, v in (params.get("properties") or {}).items()
                    },
                    "required": list(params.get("required") or []),
                },
            }
        )
    return decls


def _drain_queue(q: asyncio.Queue) -> None:
    while not q.empty():
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            break


async def main():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("[Error]: GEMINI_API_KEY not found in environment or .env file.")
        return

    client = genai.Client(api_key=api_key)
    model_id = os.getenv("CIEL_LIVE_MODEL", "gemini-3.8-live")

    print(f"\n[CIEL Live Voice]: Connecting to {model_id}...")
    print("Speak naturally (barge-in enabled). Press Ctrl+C to stop.\n")

    # Duck background mpv music while voice session is active
    duck_msg = mpv_control("duck", "18")
    did_duck = duck_msg.startswith("Ducked")
    if did_duck:
        print(f"[Audio]: {duck_msg}")

    audio_in_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=64)
    audio_out_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=256)

    def mic_callback(indata, frames, time_info, status):
        if status:
            print(f"[Mic Status]: {status}", file=sys.stderr)
        try:
            audio_in_queue.put_nowait(bytes(indata))
        except asyncio.QueueFull:
            pass

    mic_stream = sd.RawInputStream(
        samplerate=16000,
        channels=1,
        dtype="int16",
        callback=mic_callback,
        blocksize=512,
    )

    speaker_stream = sd.RawOutputStream(
        samplerate=24000,
        channels=1,
        dtype="int16",
        blocksize=1024,
    )

    decls = _build_live_function_declarations()
    live_config: dict = {
        "response_modalities": ["AUDIO"],
        "system_instruction": _build_voice_system_prompt(),
    }
    if decls:
        live_config["tools"] = [{"function_declarations": decls}]

    try:
        async with client.aio.live.connect(model=model_id, config=live_config) as session:
            mic_stream.start()
            speaker_stream.start()
            print("[CIEL Live Voice]: Online! Listening...")

            async def send_mic():
                while True:
                    data = await audio_in_queue.get()
                    await session.send(
                        input={
                            "media_chunks": [
                                {
                                    "mime_type": "audio/pcm;rate=16000",
                                    "data": data,
                                }
                            ]
                        }
                    )

            async def play_speaker():
                while True:
                    chunk = await audio_out_queue.get()
                    await asyncio.to_thread(speaker_stream.write, chunk)

            async def handle_tool_calls(tool_call_msg):
                fn_calls = getattr(tool_call_msg, "function_calls", None) or []
                responses = []
                for fc in fn_calls:
                    fn_name = getattr(fc, "name", "")
                    call_id = getattr(fc, "id", None)
                    raw_args = getattr(fc, "args", None) or {}
                    if isinstance(raw_args, str):
                        try:
                            raw_args = json.loads(raw_args)
                        except Exception:
                            raw_args = {}
                    print(f"[Voice Tool]: {fn_name}({raw_args})")
                    if fn_name in VOICE_ALLOWED_TOOLS and fn_name in tool_map:
                        try:
                            out = await asyncio.to_thread(tool_map[fn_name], **raw_args)
                        except Exception as err:
                            out = f"Tool error: {err}"
                    else:
                        out = f"Tool '{fn_name}' is not enabled in voice mode."

                    resp_obj = types.FunctionResponse(
                        id=call_id,
                        name=fn_name,
                        response={"result": str(out)[:1500]},
                    )
                    responses.append(resp_obj)

                if responses:
                    if hasattr(session, "send_tool_response"):
                        await session.send_tool_response(function_responses=responses)
                    else:
                        await session.send(
                            input=types.LiveClientToolResponse(function_responses=responses)
                        )

            async def receive_loop():
                async for response in session.receive():
                    server_content = getattr(response, "server_content", None)
                    if server_content is not None:
                        if getattr(server_content, "interrupted", False):
                            _drain_queue(audio_out_queue)
                        model_turn = getattr(server_content, "model_turn", None)
                        if model_turn is not None:
                            for part in getattr(model_turn, "parts", None) or []:
                                inline = getattr(part, "inline_data", None)
                                if inline and getattr(inline, "data", None):
                                    await audio_out_queue.put(inline.data)

                    tool_call = getattr(response, "tool_call", None)
                    if tool_call is not None:
                        asyncio.create_task(handle_tool_calls(tool_call))

            await asyncio.gather(send_mic(), play_speaker(), receive_loop())

    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"\n[Live Voice Error]: {e}")
    finally:
        _drain_queue(audio_out_queue)
        if did_duck:
            restore_msg = mpv_control("unduck")
            print(f"[Audio]: {restore_msg}")
        try:
            mic_stream.stop()
            mic_stream.close()
        except Exception:
            pass
        try:
            speaker_stream.stop()
            speaker_stream.close()
        except Exception:
            pass
        print("\n[CIEL Live Voice]: Disconnected. Returning to text chat.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[CIEL Live Voice]: Interrupted by user.")
