import os
import warnings
import torch
import soundfile as sf
import io
from kokoro import KPipeline
from huggingface_hub import snapshot_download

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

pipeline = None

VOICES = {
    "af_heart": "af_heart",
    "af_bella": "af_bella",
    "af_sky": "af_sky",
    "am_adam": "am_adam",
    "am_michael": "am_michael"
}

def init_kokoro():
    """Initialize Kokoro pipeline once."""
    global pipeline
    if pipeline is None:
        print("Downloading Kokoro model if needed...")
        snapshot_download(
            repo_id="hexgrad/Kokoro-82M",
            allow_patterns=["*.pth", "config.json", "voices/*.pt"]
        )
        pipeline = KPipeline(lang_code='a')
        print("Kokoro pipeline initialized.")

def generate_audio_stream(text: str, voice: str = "af_heart", speed: float = 1.0):
    """Generator that yields WAV bytes chunks for streaming."""
    if voice not in VOICES:
        raise ValueError(f"Invalid voice: {voice}")

    generator = pipeline(text, voice=voice, speed=speed)

    for _, _, audio in generator:
        # Convert Tensor -> NumPy if needed
        if isinstance(audio, torch.Tensor):
            audio = audio.detach().cpu().numpy()

        # Convert NumPy -> WAV bytes
        buf = io.BytesIO()
        sf.write(buf, audio, samplerate=22050, format="WAV")
        buf.seek(0)
        yield buf.read()  # yield bytes chunk
