import os
import time
import torch
import io
import re
import numpy as np
import requests
import soundfile as sf
from kokoro import KPipeline
from pydub import AudioSegment
from dotenv import load_dotenv
from huggingface_hub import snapshot_download

# --- CONFIGURATION ---
load_dotenv()

# Fetch the variables from the environment
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

pipeline = None

def init_kokoro():
    global pipeline
    if pipeline is None:
        snapshot_download(
            repo_id="hexgrad/Kokoro-82M", 
            allow_patterns=["*.pth", "config.json", "voices/*.pt"]
        )
        pipeline = KPipeline(lang_code='a')

def upload_to_telegram(audio_bytes, filename):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendAudio"
    files = {'audio': (filename, audio_bytes, 'audio/mpeg')}
    payload = {'chat_id': CHAT_ID}
    try:
        response = requests.post(url, files=files, data=payload)
        data = response.json()
        return data["result"]["audio"]["file_id"] if data.get("ok") else None
    except Exception as e:
        print(f"Telegram Error: {e}")
        return None

def generate_full_chapter(text: str, voice: str = "af_heart", speed: float = 1.0, chapter_name: str = "chapter"):
    start_perf = time.perf_counter()
    init_kokoro()
    
    sentences = re.split(r'(?<=[.!?])\s+', text.replace('\n', ' ').strip())
    combined_audio, metadata = [], []
    current_time, sample_rate = 0.0, 24000 
    silence_padding = np.zeros(int(0.15 * sample_rate))

    for i, sentence in enumerate(sentences):
        if not sentence.strip(): continue
        generator = pipeline(sentence, voice=voice, speed=speed)
        for gs, ps, audio in generator:
            if isinstance(audio, torch.Tensor):
                audio = audio.detach().cpu().numpy()
            duration = len(audio) / sample_rate
            metadata.append({
                "index": i, "text": gs.strip(),
                "start": round(current_time, 3), "end": round(current_time + duration, 3)
            })
            combined_audio.extend([audio, silence_padding])
            current_time += (duration + 0.15)

    if not combined_audio: return None, [], 0, None

    # Processing & Compression
    final_audio = np.clip(np.concatenate(combined_audio), -1.0, 1.0)
    audio_int16 = (final_audio * 32767).astype(np.int16)
    audio_segment = AudioSegment(audio_int16.tobytes(), frame_rate=sample_rate, sample_width=2, channels=1)

    buf = io.BytesIO()
    audio_segment.export(buf, format="mp3", bitrate="48k") 
    audio_data = buf.getvalue()
    
    # --- SINGLE UPLOAD LOGIC ---
    tg_file_id = upload_to_telegram(audio_data, f"{chapter_name}.mp3")
    
    return audio_data, metadata, round(time.perf_counter() - start_perf, 2), tg_file_id

def get_telegram_file_url(file_id: str):
    """Converts a Telegram file_id into a temporary downloadable URL"""
    # 1. Ask Telegram for the file path
    get_file_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}"
    
    try:
        response = requests.get(get_file_url).json()
        if response.get("ok"):
            file_path = response["result"]["file_path"]
            # 2. Construct the final download URL
            # Note: This URL is temporary (usually valid for ~1 hour)
            download_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
            return download_url
        return None
    except Exception as e:
        print(f"Error fetching from Telegram: {e}")
        return None

def generate_audio_stream(text: str, voice: str = "af_heart", speed: float = 1.0):
    """Streaming function (no Telegram upload here for speed)."""
    init_kokoro()
    generator = pipeline(text, voice=voice, speed=speed)
    for _, _, audio in generator:
        if isinstance(audio, torch.Tensor):
            audio = audio.detach().cpu().numpy()
        buf = io.BytesIO()
        sf.write(buf, audio, samplerate=24000, format="MP3")
        buf.seek(0)
        yield buf.read()