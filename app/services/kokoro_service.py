import os
import warnings
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
    global pipeline

    if pipeline is None:
        print("Downloading Kokoro model if needed...")
        snapshot_download(
            repo_id="hexgrad/Kokoro-82M",
            allow_patterns=["*.pth", "config.json", "voices/*.pt"]
        )

        pipeline = KPipeline(lang_code='a')
        print("Kokoro pipeline initialized.")

def generate_audio(text: str, voice: str, speed: float = 1.0):
    if voice not in VOICES:
        raise ValueError("Invalid voice")

    generator = pipeline(text, voice=voice, speed=speed)

    for _, _, audio in generator:
        return audio
