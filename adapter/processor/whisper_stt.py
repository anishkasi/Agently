import aiohttp
from openai import OpenAI
import io
from core import settings

client = OpenAI(api_key=settings.OPENAI_API_KEY)

async def transcribe_audio(audio_url: str, language="en") -> str:
    """Download an audio file and transcribe it using OpenAI's Whisper model."""
    async with aiohttp.ClientSession() as session:
        async with session.get(audio_url) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to download audio: {resp.status}")
            audio_bytes = await resp.read()

    file_like = io.BytesIO(audio_bytes)
    file_like.name = "audio.mp3"

    response = client.audio.transcriptions.create(
        model=settings.WHISPER_MODEL,
        file=file_like,
        language=language,
        temperature=0.0,
    )
    return getattr(response, "text", "").strip()



