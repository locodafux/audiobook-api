import uuid
import soundfile as sf
from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from app.services.kokoro_service import generate_audio

router = APIRouter(prefix="/tts", tags=["TTS"])

@router.get("/")
def tts(
    text: str = Query(...),
    voice: str = Query("af_heart"),
    speed: float = Query(1.0)
):
    audio = generate_audio(text, voice, speed)

    filename = f"audio_{uuid.uuid4().hex}.wav"
    sf.write(filename, audio, 24000)

    return FileResponse(filename, media_type="audio/wav", filename="speech.wav")
