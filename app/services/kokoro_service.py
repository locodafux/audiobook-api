import os
import time
import torch
import soundfile as sf
import io
import re
import numpy as np
from kokoro import KPipeline
from huggingface_hub import snapshot_download

pipeline = None

def init_kokoro():
    global pipeline
    if pipeline is None:
        snapshot_download(repo_id="hexgrad/Kokoro-82M", allow_patterns=["*.pth", "config.json", "voices/*.pt"])
        pipeline = KPipeline(lang_code='a')

def generate_full_chapter(text: str, voice: str = "af_heart", speed: float = 1.0):
    start_perf = time.perf_counter()
    init_kokoro()
    
    # Explicitly chunk by sentence using regex to ensure metadata matches exactly
    sentences = re.split(r'(?<=[.!?])\s+', text.replace('\n', ' ').strip())
    
    combined_audio = []
    metadata = []
    current_time = 0.0
    sample_rate = 24000 
    silence_padding = np.zeros(int(0.15 * sample_rate))

    for i, sentence in enumerate(sentences):
        if not sentence.strip(): continue
        
        # Process one sentence at a time
        generator = pipeline(sentence, voice=voice, speed=speed)
        
        for gs, ps, audio in generator:
            if isinstance(audio, torch.Tensor):
                audio = audio.detach().cpu().numpy()

            duration = len(audio) / sample_rate
            metadata.append({
                "index": i,
                "text": gs.strip(),
                "start": round(current_time, 3),
                "end": round(current_time + duration, 3)
            })

            combined_audio.append(audio)
            combined_audio.append(silence_padding)
            current_time += (duration + 0.15)

    final_audio = np.concatenate(combined_audio)
    buf = io.BytesIO()
    sf.write(buf, final_audio, samplerate=sample_rate, format="MP3")
    buf.seek(0)
    
    return buf.read(), metadata, round(time.perf_counter() - start_perf, 2)

def generate_audio_stream(text: str, voice: str = "af_heart", speed: float = 1.0):
    """Old streaming function for compatibility."""
    init_kokoro()
    generator = pipeline(text, voice=voice, speed=speed)
    for _, _, audio in generator:
        if isinstance(audio, torch.Tensor):
            audio = audio.detach().cpu().numpy()
        buf = io.BytesIO()
        sf.write(buf, audio, samplerate=24000, format="MP3")
        buf.seek(0)
        yield buf.read()