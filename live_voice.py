import asyncio
import os
import sys
from dotenv import load_dotenv
from google import genai
import sounddevice as sd

load_dotenv("/home/ilham/Projects/Ciel_V3/.env")
api_key = os.getenv("GEMINI_API_KEY")

async def main():
    if not api_key:
        print("[Error]: GEMINI_API_KEY not found in environment or .env file.")
        return

    client = genai.Client(api_key=api_key)
    model_id = "gemini-3.8-live"

    print(f"\n[CIEL Live Voice]: Connecting to {model_id}...")
    print("Press Ctrl+C to stop voice chat and return to text chat.\n")

    loop = asyncio.get_running_loop()
    audio_in_queue = asyncio.Queue()

    def mic_callback(indata, frames, time_info, status):
        if status:
            print(f"[Mic Status]: {status}", file=sys.stderr)
        # 16kHz int16 mono
        audio_in_queue.put_nowait(bytes(indata))

    # Start input stream (16kHz, mono, int16)
    mic_stream = sd.RawInputStream(
        samplerate=16000,
        channels=1,
        dtype='int16',
        callback=mic_callback,
        blocksize=512
    )

    # Start output stream (24kHz, mono, int16)
    speaker_stream = sd.RawOutputStream(
        samplerate=24000,
        channels=1,
        dtype='int16',
        blocksize=1024
    )

    try:
        async with client.aio.live.connect(
            model=model_id,
            config={'response_modalities': ['AUDIO']}
        ) as session:
            mic_stream.start()
            speaker_stream.start()
            print("[CIEL Live Voice]: Connected! Start speaking...")

            async def send_mic():
                while True:
                    data = await audio_in_queue.get()
                    await session.send(input={
                        'media_chunks': [{
                            'mime_type': 'audio/pcm;rate=16000',
                            'data': data
                        }]
                    })

            async def receive_audio():
                async for response in session.receive():
                    server_content = response.server_content
                    if server_content is not None:
                        model_turn = server_content.model_turn
                        if model_turn is not None:
                            for part in model_turn.parts:
                                if part.inline_data and part.inline_data.data:
                                    speaker_stream.write(part.inline_data.data)

            await asyncio.gather(send_mic(), receive_audio())

    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"\n[Live Voice Error]: {e}")
    finally:
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
