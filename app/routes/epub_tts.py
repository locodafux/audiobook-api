# epub_tts.py

from fastapi import APIRouter, UploadFile, File, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from app.services.kokoro_service import init_kokoro, generate_audio_stream
import base64
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import html2text
import tempfile
import os
import io
import wave
import uuid
from typing import Dict, List, Optional, Tuple
import numpy as np
import asyncio
from concurrent.futures import ThreadPoolExecutor
import functools
import time
import subprocess
import re
import platform
import psutil

router = APIRouter()

# Store chapter data temporarily
epub_cache: Dict[str, Dict] = {}

# Initialize TTS
init_kokoro()

# ----------------------------
# MacBook Optimization Functions
# ----------------------------
def get_optimal_thread_count():
    """Detect system and return optimal thread count for MacBook"""
    system = platform.system()
    
    if system == "Darwin":  # macOS
        try:
            # Try to get performance core count on Apple Silicon
            result = subprocess.run(
                ['sysctl', '-n', 'hw.perflevel0.logicalcpu'],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                perf_cores = int(result.stdout.strip())
                return perf_cores  # Use only performance cores
            
            # Fallback for Intel Macs
            result = subprocess.run(
                ['sysctl', '-n', 'hw.ncpu'],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                total_cores = int(result.stdout.strip())
                return max(2, total_cores // 2)  # Use half of cores on Intel
        except:
            pass
    
    # Default fallback
    return 4

def get_macbook_model():
    """Detect MacBook model for optimization"""
    try:
        if platform.system() == "Darwin":
            result = subprocess.run(
                ['sysctl', '-n', 'hw.model'],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                model = result.stdout.strip()
                if "MacBookAir" in model:
                    return "air"
                elif "MacBookPro" in model:
                    return "pro"
    except:
        pass
    return "unknown"

def is_macbook_on_battery():
    """Check if MacBook is running on battery"""
    try:
        battery = psutil.sensors_battery()
        if battery:
            return not battery.power_plugged
    except:
        pass
    return False

def get_adaptive_batch_size():
    """Return adaptive batch size based on system conditions"""
    model = get_macbook_model()
    on_battery = is_macbook_on_battery()
    
    if model == "air" and on_battery:
        return 2  # Conservative for MacBook Air on battery
    elif model == "air":
        return 3  # MacBook Air plugged in
    elif model == "pro" and on_battery:
        return 4  # MacBook Pro on battery
    elif model == "pro":
        return 6  # MacBook Pro plugged in
    else:
        return 3  # Default

# Configure thread pool based on MacBook specs
optimal_threads = get_optimal_thread_count()
thread_pool = ThreadPoolExecutor(max_workers=optimal_threads)

print(f"🔧 Configured for MacBook: {optimal_threads} threads, model: {get_macbook_model()}")

# ----------------------------
# EPUB Upload Endpoint
# ----------------------------
@router.post("/upload-epub")
async def upload_epub(file: UploadFile = File(...)):
    """
    Upload an EPUB file and return its chapters
    """
    if not file.filename.endswith('.epub'):
        raise HTTPException(status_code=400, detail="File must be an EPUB")
    
    try:
        # Save uploaded file temporarily
        with tempfile.NamedTemporaryFile(delete=False, suffix='.epub') as tmp_file:
            content = await file.read()
            tmp_file.write(content)
            tmp_path = tmp_file.name
        
        # Process EPUB
        book = epub.read_epub(tmp_path)
        chapters = []
        
        # Generate unique ID for this EPUB
        epub_id = str(uuid.uuid4())
        
        # Extract chapters
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                # Parse HTML content
                soup = BeautifulSoup(item.get_content(), 'html.parser')
                
                # Extract title
                title = soup.find(['h1', 'h2', 'h3'])
                title_text = title.get_text().strip() if title else f"Chapter {len(chapters) + 1}"
                
                # Remove title from content to avoid duplication
                if title:
                    title.decompose()
                
                # Convert HTML to plain text
                h = html2text.HTML2Text()
                h.ignore_links = True
                h.ignore_images = True
                text_content = h.handle(str(soup))
                
                # Clean up text
                text_content = ' '.join(text_content.split())
                
                if text_content.strip():  # Only add non-empty chapters
                    chapters.append({
                        'title': title_text,
                        'content': text_content,
                        'index': len(chapters)
                    })
        
        # Clean up temp file
        os.unlink(tmp_path)
        
        # Store in cache
        epub_cache[epub_id] = {
            'chapters': chapters,
            'title': book.get_metadata('DC', 'title')[0][0] if book.get_metadata('DC', 'title') else 'Unknown Title',
            'author': book.get_metadata('DC', 'creator')[0][0] if book.get_metadata('DC', 'creator') else 'Unknown Author'
        }
        
        return JSONResponse({
            'epub_id': epub_id,
            'title': epub_cache[epub_id]['title'],
            'author': epub_cache[epub_id]['author'],
            'chapters': [{'index': c['index'], 'title': c['title']} for c in chapters]
        })
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing EPUB: {str(e)}")

# ----------------------------
# Audio Processing Functions
# ----------------------------
def bytes_to_numpy(audio_bytes: bytes) -> np.ndarray:
    """Convert bytes to numpy array"""
    return np.frombuffer(audio_bytes, dtype=np.int16).copy()

def numpy_to_bytes(audio_array: np.ndarray) -> bytes:
    """Convert numpy array to bytes"""
    return audio_array.astype(np.int16).tobytes()

def remove_click_sound(audio_chunks: List[bytes], fade_ms: int = 10) -> List[bytes]:
    """
    Remove click sounds between audio chunks by applying crossfade
    """
    if len(audio_chunks) <= 1:
        return audio_chunks
    
    processed_chunks = []
    
    for i, chunk in enumerate(audio_chunks):
        if i == 0:
            # First chunk - just add it
            processed_chunks.append(chunk)
        else:
            # Apply fade out to previous chunk and fade in to current chunk
            prev_chunk = processed_chunks[-1]
            
            # Convert to numpy arrays for processing (make copies to avoid read-only issues)
            prev_array = bytes_to_numpy(prev_chunk)
            curr_array = bytes_to_numpy(chunk)
            
            # Calculate fade samples
            sample_rate = 24000  # Adjust based on your TTS output
            fade_samples = int(sample_rate * fade_ms / 1000)
            
            # Ensure we don't fade more than half the chunk
            fade_samples = min(fade_samples, len(prev_array) // 4, len(curr_array) // 4)
            
            if fade_samples > 0:
                # Create fade curves
                fade_out = np.linspace(1, 0, fade_samples)
                fade_in = np.linspace(0, 1, fade_samples)
                
                # Create new arrays with fades applied
                prev_faded = prev_array.copy()
                curr_faded = curr_array.copy()
                
                # Apply fades to the copies
                prev_faded[-fade_samples:] = (prev_array[-fade_samples:] * fade_out).astype(np.int16)
                curr_faded[:fade_samples] = (curr_array[:fade_samples] * fade_in).astype(np.int16)
                
                # Convert back to bytes and replace
                processed_chunks[-1] = numpy_to_bytes(prev_faded)
                processed_chunks.append(numpy_to_bytes(curr_faded))
            else:
                processed_chunks.append(chunk)
    
    return processed_chunks

def add_silence_between_sentences(audio_chunks: List[bytes], silence_ms: int = 300) -> List[bytes]:
    """
    Add small silence between sentences for better listening experience
    """
    if len(audio_chunks) <= 1 or silence_ms == 0:
        return audio_chunks
    
    sample_rate = 24000
    sample_width = 2  # 16-bit
    channels = 1
    
    # Create silence
    silence_samples = int(sample_rate * silence_ms / 1000)
    silence = b'\x00' * (silence_samples * sample_width * channels)
    
    result = []
    for i, chunk in enumerate(audio_chunks):
        result.append(chunk)
        if i < len(audio_chunks) - 1:
            result.append(silence)
    
    return result

def combine_audio_chunks(chunks: List[bytes]) -> bytes:
    """Combine multiple audio chunks into one continuous audio stream"""
    return b''.join(chunks)

# ----------------------------
# Temperature-Aware Concurrent Audio Generation
# ----------------------------
class TemperatureMonitor:
    """Monitor system temperature and adjust concurrency"""
    
    def __init__(self, max_temp: float = 85.0):
        self.max_temp = max_temp
        self.high_temp_count = 0
    
    async def get_cpu_temperature(self) -> Optional[float]:
        """Get CPU temperature on macOS"""
        if platform.system() != "Darwin":
            return None
        
        try:
            # Try using osx-cpu-temp if available
            result = subprocess.run(
                ['osx-cpu-temp'],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                # Parse temperature (format: "65.5°C")
                temp_str = result.stdout.strip().replace('°C', '')
                return float(temp_str)
        except:
            pass
        
        # Fallback: use psutil sensors (may not work on all Macs)
        try:
            temps = psutil.sensors_temperatures()
            if 'cpu_thermal' in temps:
                return temps['cpu_thermal'][0].current
        except:
            pass
        
        return None
    
    async def should_throttle(self) -> bool:
        """Check if we should throttle due to high temperature"""
        temp = await self.get_cpu_temperature()
        if temp and temp > self.max_temp:
            self.high_temp_count += 1
            if self.high_temp_count > 3:
                return True
        else:
            self.high_temp_count = max(0, self.high_temp_count - 1)
        
        return False
    
    async def cool_down(self):
        """Cool down period if temperature is too high"""
        if await self.should_throttle():
            print("🌡️ Temperature high, cooling down for 2 seconds...")
            await asyncio.sleep(2)

# Initialize temperature monitor
temp_monitor = TemperatureMonitor()

async def generate_sentence_audio(sentence: str, voice: str, speed: float) -> List[bytes]:
    """Generate audio for a single sentence (async wrapper)"""
    loop = asyncio.get_event_loop()
    
    # Check temperature before processing
    await temp_monitor.cool_down()
    
    # Run TTS generation in thread pool
    def _generate():
        chunks = []
        for chunk_bytes in generate_audio_stream(sentence, voice, speed):
            chunks.append(chunk_bytes)
        return chunks
    
    return await loop.run_in_executor(thread_pool, _generate)

async def generate_chapter_audio_concurrent(chapter: Dict, voice: str, speed: float) -> Tuple[int, List[bytes]]:
    """Generate audio for a whole chapter concurrently"""
    text_content = chapter['content']
    chapter_index = chapter['index']
    chapter_title = chapter['title']
    
    print(f"📖 Processing chapter: {chapter_title}")
    
    # Split into sentences
    sentences = [s.strip() + '.' for s in text_content.split('.') if s.strip()]
    
    if not sentences:
        return chapter_index, []
    
    # Get adaptive batch size based on system conditions
    batch_size = get_adaptive_batch_size()
    
    # Process sentences in batches to avoid overwhelming the system
    all_chunks = []
    
    for i in range(0, len(sentences), batch_size):
        batch = sentences[i:i+batch_size]
        
        # Generate audio for batch concurrently
        tasks = [generate_sentence_audio(sentence, voice, speed) for sentence in batch]
        batch_chunks_list = await asyncio.gather(*tasks)
        
        # Flatten and add to results
        for chunks in batch_chunks_list:
            all_chunks.extend(chunks)
        
        # Small delay between batches if on battery
        if is_macbook_on_battery():
            await asyncio.sleep(0.1)
    
    # Remove click sounds within chapter
    all_chunks = remove_click_sound(all_chunks)
    
    return chapter_index, all_chunks

async def generate_chapters_concurrent_smart(chapters: List[Dict], voice: str, speed: float) -> Dict[int, List[bytes]]:
    """Generate audio for multiple chapters with smart batching"""
    all_chunks = {}
    
    # Get adaptive batch size
    batch_size = get_adaptive_batch_size()
    
    print(f"🚀 Processing {len(chapters)} chapters with batch size {batch_size}")
    
    # Process chapters in batches
    for i in range(0, len(chapters), batch_size):
        batch = chapters[i:i+batch_size]
        print(f"📚 Processing batch {i//batch_size + 1}/{(len(chapters)-1)//batch_size + 1}")
        
        # Process batch concurrently
        batch_tasks = [generate_chapter_audio_concurrent(ch, voice, speed) for ch in batch]
        batch_results = await asyncio.gather(*batch_tasks)
        
        # Add to results
        for idx, chunks in batch_results:
            all_chunks[idx] = chunks
        
        # Check temperature and cool down if needed
        if await temp_monitor.should_throttle():
            print("🌡️ System warm, taking a short break...")
            await asyncio.sleep(3)
        elif i < len(chapters) - batch_size:
            # Small delay between batches
            await asyncio.sleep(0.5)
    
    return all_chunks

# ----------------------------
# Generate Chapter Range Audio Endpoint (Optimized for MacBook)
# ----------------------------
@router.get("/generate-chapter-range")
async def generate_chapter_range(
    epub_id: str,
    from_chapter: int = Query(..., description="Starting chapter index (0-based)"),
    to_chapter: int = Query(..., description="Ending chapter index (0-based)"),
    voice: str = "af_heart",
    speed: float = 1.0,
    format: str = "mp3",
    include_silence: bool = True,
    silence_ms: int = 300
):
    """
    Generate audio for a range of chapters and return as downloadable file
    Optimized for MacBook with temperature awareness
    """
    # Validate EPUB
    if epub_id not in epub_cache:
        raise HTTPException(status_code=404, detail="EPUB not found. Please upload again.")
    
    chapters = epub_cache[epub_id]['chapters']
    
    # Validate chapter range
    if from_chapter < 0 or to_chapter >= len(chapters) or from_chapter > to_chapter:
        raise HTTPException(status_code=404, detail="Invalid chapter range")
    
    book_title = epub_cache[epub_id]['title']
    selected_chapters = chapters[from_chapter:to_chapter + 1]
    
    # Show system info
    model = get_macbook_model()
    on_battery = is_macbook_on_battery()
    print(f"💻 Running on MacBook {model.upper()} {'(battery)' if on_battery else '(plugged in)'}")
    
    try:
        start_time = time.time()
        
        # Generate audio for all chapters with smart batching
        chapter_audio = await generate_chapters_concurrent_smart(selected_chapters, voice, speed)
        
        # Combine chapters in order
        all_audio_chunks = []
        
        for idx, chapter in enumerate(selected_chapters):
            chapter_index = chapter['index']
            chapter_chunks = chapter_audio.get(chapter_index, [])
            
            # Add chapter chunks
            all_audio_chunks.extend(chapter_chunks)
            
            # Add longer silence between chapters
            if idx < len(selected_chapters) - 1 and include_silence:
                sample_rate = 24000
                sample_width = 2
                channels = 1
                chapter_gap_ms = 1000  # 1 second between chapters
                silence = b'\x00' * (int(sample_rate * chapter_gap_ms / 1000) * sample_width * channels)
                all_audio_chunks.append(silence)
        
        # Add optional silence between sentences
        if include_silence and silence_ms > 0:
            all_audio_chunks = add_silence_between_sentences(all_audio_chunks, silence_ms)
        
        elapsed_time = time.time() - start_time
        minutes = int(elapsed_time // 60)
        seconds = int(elapsed_time % 60)
        print(f"✅ Generated {len(selected_chapters)} chapters in {minutes}m {seconds}s")
        
        # Create filename based on chapter range
        if from_chapter == to_chapter:
            filename_base = f"{book_title} - {selected_chapters[0]['title']}"
        else:
            filename_base = f"{book_title} - Chapters {from_chapter + 1} to {to_chapter + 1}"
        
        # Clean filename
        filename_base = "".join(c for c in filename_base if c.isalnum() or c in (' ', '-', '_')).rstrip()
        
        # Generate audio file
        if format.lower() == "wav":
            return create_wav_response(all_audio_chunks, filename_base)
        else:
            return await create_mp3_response_async(all_audio_chunks, filename_base)
            
    except Exception as e:
        print(f"❌ Error generating audio: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error generating audio: {str(e)}")

# ----------------------------
# Optimized MP3 Conversion
# ----------------------------
async def convert_to_mp3_concurrent(audio_chunks: List[bytes]) -> Optional[bytes]:
    """Convert audio chunks to MP3 using concurrent processing"""
    try:
        from pydub import AudioSegment
        import io
        
        loop = asyncio.get_event_loop()
        
        def _convert():
            # Combine chunks in groups for parallel processing
            chunk_size = 5  # Process 5 chunks at a time for MacBook
            segments = []
            
            for i in range(0, len(audio_chunks), chunk_size):
                group = audio_chunks[i:i + chunk_size]
                group_audio = AudioSegment.empty()
                
                for chunk in group:
                    chunk_io = io.BytesIO(chunk)
                    segment = AudioSegment.from_wav(chunk_io)
                    group_audio += segment
                
                segments.append(group_audio)
            
            # Combine all segments
            if len(segments) == 1:
                combined = segments[0]
            else:
                combined = segments[0]
                for segment in segments[1:]:
                    combined += segment
            
            # Export to MP3 with optimal settings
            mp3_io = io.BytesIO()
            combined.export(
                mp3_io, 
                format="mp3", 
                bitrate="192k", 
                parameters=["-q:a", "0", "-compression_level", "0"]
            )
            return mp3_io.getvalue()
        
        return await loop.run_in_executor(thread_pool, _convert)
        
    except ImportError as e:
        print(f"⚠️ pydub not installed: {e}")
        return None
    except Exception as e:
        print(f"⚠️ MP3 conversion failed: {e}")
        return None

def create_wav_response(audio_chunks, filename_base):
    """Combine audio chunks into a WAV file"""
    wav_io = io.BytesIO()
    
    # WAV parameters
    sample_rate = 24000
    channels = 1
    sample_width = 2  # 16-bit
    
    # Combine all chunks first
    combined_audio = combine_audio_chunks(audio_chunks)
    
    with wave.open(wav_io, 'wb') as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(combined_audio)
    
    wav_io.seek(0)
    
    filename = f"{filename_base}.wav"
    return StreamingResponse(
        wav_io,
        media_type="audio/wav",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

async def create_mp3_response_async(audio_chunks, filename_base):
    """Convert WAV chunks to MP3 asynchronously"""
    mp3_data = await convert_to_mp3_concurrent(audio_chunks)
    
    if mp3_data:
        filename = f"{filename_base}.mp3"
        return StreamingResponse(
            io.BytesIO(mp3_data),
            media_type="audio/mpeg",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    else:
        print("⚠️ Falling back to WAV format")
        return create_wav_response(audio_chunks, filename_base)

# ----------------------------
# Get Chapter List Endpoint
# ----------------------------
@router.get("/get-chapters/{epub_id}")
async def get_chapters(epub_id: str):
    """Get list of chapters for an uploaded EPUB"""
    if epub_id not in epub_cache:
        raise HTTPException(status_code=404, detail="EPUB not found")
    
    return JSONResponse({
        'epub_id': epub_id,
        'title': epub_cache[epub_id]['title'],
        'author': epub_cache[epub_id]['author'],
        'chapters': [{'index': c['index'], 'title': c['title']} for c in epub_cache[epub_id]['chapters']]
    })

# ----------------------------
# System Info Endpoint (Optional)
# ----------------------------
@router.get("/system-info")
async def get_system_info():
    """Get system information for debugging"""
    return {
        "platform": platform.system(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "macbook_model": get_macbook_model(),
        "optimal_threads": optimal_threads,
        "on_battery": is_macbook_on_battery(),
        "adaptive_batch_size": get_adaptive_batch_size(),
        "cpu_count": os.cpu_count(),
        "memory": f"{psutil.virtual_memory().total / (1024**3):.1f} GB"
    }

# ----------------------------
# Cleanup on shutdown
# ----------------------------
@router.on_event("shutdown")
async def shutdown_event():
    """Clean up thread pools on shutdown"""
    thread_pool.shutdown(wait=True)
    print("🧹 Cleaned up thread pools")

# ----------------------------
# HTML Test Page (Keep the same as before)
# ----------------------------
@router.get("/epub-reader")
async def epub_reader():
    # ... (keep the same HTML from previous response)
    # The HTML remains unchanged from the previous version
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>EPUB to Audio - Chapter Range Converter</title>
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }
            .container { max-width: 1000px; margin: auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            h1 { color: #333; border-bottom: 2px solid #4CAF50; padding-bottom: 10px; }
            
            #upload-area { 
                border: 3px dashed #4CAF50; 
                padding: 40px; 
                text-align: center;
                margin: 20px 0;
                cursor: pointer;
                border-radius: 10px;
                background: #f9f9f9;
                transition: all 0.3s;
            }
            #upload-area:hover { 
                border-color: #45a049; 
                background: #e8f5e9;
            }
            #upload-area.dragover { 
                border-color: #2196F3; 
                background: #e3f2fd;
            }
            
            .book-info {
                background: #e8f5e9;
                padding: 15px;
                border-radius: 5px;
                margin: 20px 0;
            }
            
            .chapter-range {
                background: #f5f5f5;
                padding: 20px;
                border-radius: 5px;
                margin: 20px 0;
            }
            
            .range-selector {
                display: flex;
                gap: 20px;
                align-items: center;
                flex-wrap: wrap;
            }
            
            .range-selector select {
                padding: 10px;
                border: 1px solid #ddd;
                border-radius: 4px;
                min-width: 250px;
            }
            
            .chapter-item { 
                padding: 10px; 
                margin: 5px 0; 
                background: #f9f9f9; 
                border-radius: 4px;
                border-left: 3px solid #4CAF50;
            }
            
            .controls {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 15px;
                margin: 20px 0;
                padding: 20px;
                background: #f5f5f5;
                border-radius: 5px;
            }
            
            .control-group {
                display: flex;
                flex-direction: column;
            }
            
            .control-group label {
                font-weight: bold;
                margin-bottom: 5px;
                color: #555;
            }
            
            .control-group select, .control-group input {
                padding: 8px;
                border: 1px solid #ddd;
                border-radius: 4px;
            }
            
            .generate-btn { 
                background: #4CAF50; 
                color: white; 
                border: none;
                padding: 15px 30px;
                border-radius: 5px;
                cursor: pointer;
                font-size: 16px;
                font-weight: bold;
                width: 100%;
                transition: background 0.3s;
            }
            .generate-btn:hover { background: #45a049; }
            .generate-btn:disabled {
                background: #cccccc;
                cursor: not-allowed;
            }
            
            #audio-player { 
                width: 100%;
                margin: 20px 0;
            }
            
            #status { 
                margin: 20px 0; 
                padding: 15px; 
                background: #e3f2fd; 
                border-radius: 5px;
                border-left: 4px solid #2196F3;
            }
            
            #loading { 
                display: none;
                text-align: center;
                margin: 30px 0;
            }
            
            .spinner {
                border: 5px solid #f3f3f3;
                border-top: 5px solid #4CAF50;
                border-radius: 50%;
                width: 50px;
                height: 50px;
                animation: spin 1s linear infinite;
                margin: auto;
            }
            
            @keyframes spin {
                0% { transform: rotate(0deg); }
                100% { transform: rotate(360deg); }
            }
            
            .progress-bar {
                width: 100%;
                height: 20px;
                background: #f0f0f0;
                border-radius: 10px;
                overflow: hidden;
                margin: 10px 0;
            }
            
            .progress-fill {
                height: 100%;
                background: #4CAF50;
                width: 0%;
                transition: width 0.3s;
            }
            
            .download-section {
                margin-top: 20px;
                padding: 20px;
                background: #e8f5e9;
                border-radius: 5px;
                display: none;
            }
            
            .download-btn {
                background: #2196F3;
                color: white;
                border: none;
                padding: 10px 20px;
                border-radius: 4px;
                cursor: pointer;
                font-size: 14px;
                margin-right: 10px;
            }
            
            .download-btn:hover {
                background: #1976D2;
            }
            
            .chapter-count {
                font-size: 14px;
                color: #666;
                margin-top: 5px;
            }
            
            .error {
                color: #f44336;
                background: #ffebee;
                padding: 10px;
                border-radius: 4px;
                margin: 10px 0;
            }
            
            .system-info {
                font-size: 12px;
                color: #888;
                margin-top: 20px;
                padding: 10px;
                background: #f9f9f9;
                border-radius: 4px;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📚 EPUB to Audio Converter</h1>
            <p>Convert any chapter range from your EPUB to high-quality audio</p>
            <div id="system-info" class="system-info">Loading system info...</div>
            
            <!-- Upload Area -->
            <div id="upload-area" ondragover="handleDragOver(event)" ondragleave="handleDragLeave(event)" ondrop="handleDrop(event)" onclick="document.getElementById('file-input').click()">
                <input type="file" id="file-input" accept=".epub" style="display: none;" onchange="handleFileSelect(event)">
                <div style="font-size: 48px;">📖</div>
                <p><strong>Drag and drop an EPUB file here</strong></p>
                <p>or click to browse</p>
                <p id="file-name" style="color: #4CAF50; font-weight: bold;"></p>
            </div>
            
            <!-- Book Info -->
            <div id="book-info" class="book-info" style="display: none;">
                <h2 id="book-title"></h2>
                <p><strong>Author:</strong> <span id="book-author"></span></p>
                <p><strong>Chapters:</strong> <span id="chapter-count"></span></p>
                <p id="epub-id" style="display: none;"></p>
            </div>
            
            <!-- Controls -->
            <div class="controls" id="controls" style="display: none;">
                <div class="control-group">
                    <label>Voice:</label>
                    <select id="voice">
                        <option value="af_heart">❤️ af_heart (Female - Heart)</option>
                        <option value="af_bella">🎀 af_bella (Female - Bella)</option>
                        <option value="af_sky">☁️ af_sky (Female - Sky)</option>
                        <option value="am_adam">👨 am_adam (Male - Adam)</option>
                        <option value="am_michael">👔 am_michael (Male - Michael)</option>
                    </select>
                </div>
                
                <div class="control-group">
                    <label>Speed:</label>
                    <select id="speed">
                        <option value="0.75">🐢 0.75x (Slower)</option>
                        <option value="1.0" selected>⚡ 1.0x (Normal)</option>
                        <option value="1.25">🚀 1.25x (Fast)</option>
                        <option value="1.5">🔥 1.5x (Faster)</option>
                    </select>
                </div>
                
                <div class="control-group">
                    <label>Format:</label>
                    <select id="format">
                        <option value="mp3">🎵 MP3 (Smaller size)</option>
                        <option value="wav">🎼 WAV (Best quality)</option>
                    </select>
                </div>
                
                <div class="control-group">
                    <label>Silence between sentences:</label>
                    <select id="silence">
                        <option value="0">No silence</option>
                        <option value="200">0.2 seconds</option>
                        <option value="300" selected>0.3 seconds</option>
                        <option value="500">0.5 seconds</option>
                        <option value="1000">1 second</option>
                    </select>
                </div>
            </div>
            
            <!-- Chapter Range Selection -->
            <div id="range-selection" class="chapter-range" style="display: none;">
                <h3>📑 Select Chapter Range</h3>
                <div class="range-selector">
                    <div style="flex: 1;">
                        <label><strong>From Chapter:</strong></label>
                        <select id="from-chapter" style="width: 100%;"></select>
                    </div>
                    <div style="flex: 1;">
                        <label><strong>To Chapter:</strong></label>
                        <select id="to-chapter" style="width: 100%;"></select>
                    </div>
                    <div style="flex: 2;">
                        <button class="generate-btn" onclick="generateAudio()" id="generate-btn">
                            🔊 Generate Audio for Selected Range
                        </button>
                    </div>
                </div>
                <div id="range-info" class="chapter-count" style="margin-top: 10px;"></div>
            </div>
            
            <!-- Chapter List (for reference) -->
            <div id="chapter-list" style="margin: 20px 0; max-height: 300px; overflow-y: auto; border: 1px solid #eee; padding: 10px;"></div>
            
            <!-- Progress Bar -->
            <div class="progress-bar" id="progress-container" style="display: none;">
                <div id="progress" class="progress-fill"></div>
            </div>
            
            <!-- Loading Spinner -->
            <div id="loading">
                <div class="spinner"></div>
                <p id="loading-text">Generating audio... This may take a moment</p>
            </div>
            
            <!-- Download Section -->
            <div id="download-section" class="download-section">
                <h3>✅ Audio Generated Successfully!</h3>
                <audio id="audio-player" controls></audio>
                <div style="margin-top: 15px;">
                    <button class="download-btn" id="download-btn" onclick="downloadAudio()">⬇️ Download Audio</button>
                    <button class="download-btn" id="play-btn" onclick="playAudio()">▶️ Play in Browser</button>
                </div>
                <p id="file-info" style="margin-top: 10px; color: #666;"></p>
            </div>
            
            <!-- Status -->
            <div id="status">Ready to upload EPUB</div>
        </div>

        <script>
            let currentEpubId = null;
            let chapters = [];
            let currentAudioBlob = null;
            let currentFilename = '';
            
            // Load system info
            fetch('/system-info')
                .then(res => res.json())
                .then(data => {
                    const battery = data.on_battery ? '🔋 Battery' : '⚡ Plugged in';
                    document.getElementById('system-info').innerHTML = 
                        `💻 ${data.macbook_model.toUpperCase()} Mac | ${data.optimal_threads} threads | ${battery} | ${data.memory} RAM`;
                });
            
            // File upload handling
            function handleDragOver(e) {
                e.preventDefault();
                document.getElementById('upload-area').classList.add('dragover');
            }
            
            function handleDragLeave(e) {
                e.preventDefault();
                document.getElementById('upload-area').classList.remove('dragover');
            }
            
            function handleDrop(e) {
                e.preventDefault();
                document.getElementById('upload-area').classList.remove('dragover');
                const files = e.dataTransfer.files;
                if (files.length > 0) {
                    uploadFile(files[0]);
                }
            }
            
            function handleFileSelect(e) {
                const files = e.target.files;
                if (files.length > 0) {
                    uploadFile(files[0]);
                }
            }
            
            async function uploadFile(file) {
                if (!file.name.endsWith('.epub')) {
                    showError('Please select a valid EPUB file');
                    return;
                }
                
                document.getElementById('file-name').textContent = '📁 ' + file.name;
                updateStatus('📤 Uploading EPUB...');
                
                const formData = new FormData();
                formData.append('file', file);
                
                try {
                    const response = await fetch('/upload-epub', {
                        method: 'POST',
                        body: formData
                    });
                    
                    if (!response.ok) throw new Error('Upload failed');
                    
                    const data = await response.json();
                    currentEpubId = data.epub_id;
                    chapters = data.chapters;
                    
                    document.getElementById('book-title').textContent = data.title;
                    document.getElementById('book-author').textContent = data.author || 'Unknown';
                    document.getElementById('chapter-count').textContent = chapters.length;
                    document.getElementById('epub-id').textContent = data.epub_id;
                    document.getElementById('book-info').style.display = 'block';
                    document.getElementById('controls').style.display = 'grid';
                    document.getElementById('range-selection').style.display = 'block';
                    
                    populateChapterSelectors();
                    displayChapterList(chapters);
                    updateStatus(`✅ Loaded: ${data.title} by ${data.author || 'Unknown'} (${chapters.length} chapters)`);
                    
                } catch (error) {
                    showError('Upload failed: ' + error.message);
                }
            }
            
            function populateChapterSelectors() {
                const fromSelect = document.getElementById('from-chapter');
                const toSelect = document.getElementById('to-chapter');
                
                fromSelect.innerHTML = '';
                toSelect.innerHTML = '';
                
                chapters.forEach((chapter, index) => {
                    const option1 = new Option(`${index + 1}. ${chapter.title}`, index);
                    const option2 = new Option(`${index + 1}. ${chapter.title}`, index);
                    fromSelect.add(option1);
                    toSelect.add(option2);
                });
                
                // Set default range (first 3 chapters or all if less)
                if (chapters.length > 0) {
                    toSelect.value = Math.min(2, chapters.length - 1);
                    updateRangeInfo();
                }
            }
            
            function updateRangeInfo() {
                const from = parseInt(document.getElementById('from-chapter').value);
                const to = parseInt(document.getElementById('to-chapter').value);
                const count = to - from + 1;
                
                if (chapters.length > 0) {
                    document.getElementById('range-info').textContent = 
                        `Selected: ${count} chapter${count > 1 ? 's' : ''} (${chapters[from].title} → ${chapters[to].title})`;
                }
            }
            
            document.getElementById('from-chapter').addEventListener('change', updateRangeInfo);
            document.getElementById('to-chapter').addEventListener('change', updateRangeInfo);
            
            function displayChapterList(chapters) {
                const list = document.getElementById('chapter-list');
                list.innerHTML = '<h4>📋 All Chapters:</h4>';
                
                chapters.forEach(chapter => {
                    const div = document.createElement('div');
                    div.className = 'chapter-item';
                    div.innerHTML = `<strong>${chapter.index + 1}.</strong> ${chapter.title}`;
                    list.appendChild(div);
                });
            }
            
            async function generateAudio() {
                const fromChapter = parseInt(document.getElementById('from-chapter').value);
                const toChapter = parseInt(document.getElementById('to-chapter').value);
                const voice = document.getElementById('voice').value;
                const speed = document.getElementById('speed').value;
                const format = document.getElementById('format').value;
                const silence = parseInt(document.getElementById('silence').value);
                
                if (fromChapter > toChapter) {
                    showError('From chapter must be less than or equal to to chapter');
                    return;
                }
                
                // Show loading
                document.getElementById('loading').style.display = 'block';
                document.getElementById('progress-container').style.display = 'block';
                document.getElementById('download-section').style.display = 'none';
                document.getElementById('generate-btn').disabled = true;
                
                const totalChapters = toChapter - fromChapter + 1;
                document.getElementById('loading-text').textContent = 
                    `Generating audio for ${totalChapters} chapter${totalChapters > 1 ? 's' : ''}... This may take a few minutes.`;
                
                let progress = 0;
                document.getElementById('progress').style.width = '0%';
                
                // Simulate progress
                const progressInterval = setInterval(() => {
                    if (progress < 90) {
                        progress += 5;
                        document.getElementById('progress').style.width = progress + '%';
                    }
                }, 2000);
                
                try {
                    // Build URL with parameters
                    const url = `/generate-chapter-range?epub_id=${currentEpubId}` +
                        `&from_chapter=${fromChapter}&to_chapter=${toChapter}` +
                        `&voice=${voice}&speed=${speed}&format=${format}` +
                        `&include_silence=true&silence_ms=${silence}`;
                    
                    const response = await fetch(url);
                    
                    clearInterval(progressInterval);
                    
                    if (!response.ok) {
                        const errorData = await response.json();
                        throw new Error(errorData.detail || 'Failed to generate audio');
                    }
                    
                    document.getElementById('progress').style.width = '100%';
                    
                    // Get the audio blob
                    currentAudioBlob = await response.blob();
                    
                    // Create filename
                    if (fromChapter === toChapter) {
                        currentFilename = `${chapters[fromChapter].title.replace(/[^a-zA-Z0-9]/g, '_')}.${format}`;
                    } else {
                        currentFilename = `Chapters_${fromChapter + 1}_to_${toChapter + 1}.${format}`;
                    }
                    
                    // Update download section
                    document.getElementById('download-section').style.display = 'block';
                    document.getElementById('file-info').textContent = 
                        `File size: ${(currentAudioBlob.size / (1024 * 1024)).toFixed(2)} MB | Format: ${format.toUpperCase()}`;
                    
                    // Create audio URL for playback
                    const audioUrl = URL.createObjectURL(currentAudioBlob);
                    document.getElementById('audio-player').src = audioUrl;
                    
                    updateStatus(`✅ Successfully generated audio for ${totalChapters} chapter${totalChapters > 1 ? 's' : ''}!`);
                    
                } catch (error) {
                    clearInterval(progressInterval);
                    showError('Generation failed: ' + error.message);
                } finally {
                    document.getElementById('loading').style.display = 'none';
                    document.getElementById('progress-container').style.display = 'none';
                    document.getElementById('generate-btn').disabled = false;
                }
            }
            
            function playAudio() {
                if (currentAudioBlob) {
                    const audioUrl = URL.createObjectURL(currentAudioBlob);
                    const audioPlayer = document.getElementById('audio-player');
                    audioPlayer.src = audioUrl;
                    audioPlayer.play();
                }
            }
            
            function downloadAudio() {
                if (currentAudioBlob) {
                    const audioUrl = URL.createObjectURL(currentAudioBlob);
                    const a = document.createElement('a');
                    a.href = audioUrl;
                    a.download = currentFilename;
                    a.click();
                }
            }
            
            function updateStatus(message) {
                document.getElementById('status').textContent = message;
            }
            
            function showError(message) {
                document.getElementById('status').innerHTML = `<span style="color: #f44336;">❌ ${message}</span>`;
            }
        </script>
    </body>
    </html>
    """
    return HTMLResponse(html)
