import os
import warnings
from kokoro import KPipeline
import soundfile as sf
from huggingface_hub import snapshot_download

# 1. SILENCE WARNINGS & SET TOKEN
# This hides the 'unauthenticated' warning and other messy logs
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

# 2. ENSURE MODEL IS DOWNLOADED
# This checks and downloads the ~80MB model if it's missing
print("Checking for model weights...")
snapshot_download(
    repo_id="hexgrad/Kokoro-82M", 
    allow_patterns=["*.pth", "config.json", "voices/*.pt"]
)

# 3. POPULAR AMERICAN VOICES
VOICES = {
    "1": "af_heart",   # Female (High Quality)
    "2": "af_bella",   # Female (Clear)
    "3": "af_sky",     # Female
    "4": "am_adam",    # Male
    "5": "am_michael"  # Male
}

print("\n--- Kokoro Voice Selector ---")
for key, name in VOICES.items():
    print(f"[{key}] {name}")

choice = input("\nSelect a voice number (default 1): ") or "1"
voice_name = VOICES.get(choice, "af_heart")

# 4. INITIALIZE PIPELINE
# 'a' stands for American English
pipeline = KPipeline(lang_code='a') 

text = input("What should I say? ") or "Hello Leo, your Kokoro setup is complete."

print(f"🎙️ Using voice: {voice_name}...")
generator = pipeline(text, voice=voice_name, speed=1)

# 5. GENERATE AND PLAY
for i, (gs, ps, audio) in enumerate(generator):
    filename = "output.wav"
    sf.write(filename, audio, 24000)
    # afplay is the built-in Mac audio player
    os.system(f"afplay {filename}")

print("✨ Done!")
