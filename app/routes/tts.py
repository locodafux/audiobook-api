import base64
import os
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from ebooklib import epub
from bs4 import BeautifulSoup
from pathlib import Path

# Importing the service
from app.services.kokoro_service import generate_full_chapter

router = APIRouter()

# --- DYNAMIC PATH LOGIC ---
# This looks at the current file (tts.py), goes up one to 'routes', 
# then up again to 'app', then into 'storage/books'
CURRENT_FILE = Path(__file__).resolve()
APP_ROOT = CURRENT_FILE.parent.parent  # This is the 'app' folder
BOOK_PATH = os.path.join(APP_ROOT, "storage", "books", "mvs-1401-2100.epub")

print(f"DEBUG: System is looking for EPUB at: {BOOK_PATH}")

class ChapterRequest(BaseModel):
    text: str
    voice: str = "af_heart"
    speed: float = 1.0

# --- API ENDPOINTS ---

@router.get("/book/chapters")
async def get_book_chapters():
    if not os.path.exists(BOOK_PATH):
        raise HTTPException(status_code=404, detail="EPUB not found")
    
    try:
        book = epub.read_epub(BOOK_PATH)
        
        # 1. Create a map of href -> Title from the Table of Contents
        title_map = {}
        def process_toc(toc_list):
            for item in toc_list:
                if isinstance(item, tuple):
                    title_map[item[0].href] = item[0].title
                    if len(item) > 1: process_toc(item[1])
                elif hasattr(item, 'href'):
                    title_map[item.href] = item.title

        process_toc(book.toc)

        chapters = []
        # 2. Iterate through documents and use the map for pretty titles
        for item in book.get_items_of_type(9):
            # Use the TOC title if available, otherwise clean up the filename
            pretty_name = title_map.get(item.get_name(), item.get_name())
            
            # Clean up common junk (remove file extensions and OEBPS/ prefix)
            if pretty_name == item.get_name():
                pretty_name = pretty_name.replace('OEBPS/', '').replace('.html', '').replace('.xhtml', '').replace('-', ' ').title()

            chapters.append({
                "id": item.get_id(),
                "name": pretty_name
            })
            
        return chapters
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
        
@router.get("/book/chapter-content/{item_id}")
async def get_chapter_content(item_id: str):
    try:
        book = epub.read_epub(BOOK_PATH)
        item = book.get_item_with_id(item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Chapter item not found")
        
        soup = BeautifulSoup(item.get_content(), 'html.parser')
        clean_text = " ".join(soup.get_text(separator=' ').split())
        return {"content": clean_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/tts/chapter")
async def post_chapter(request: ChapterRequest):
    try:
        audio_bytes, metadata, gen_time = generate_full_chapter(
            request.text, request.voice, request.speed
        )
        return {
            "audio": base64.b64encode(audio_bytes).decode("utf-8"),
            "metadata": metadata,
            "generation_time": gen_time
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- FRONTEND UI ---

@router.get("/tts-test")
@router.get("/tts-test")
async def tts_test_page():
    return HTMLResponse("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Kokoro Auto-Player</title>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
        <style>
            :root { --accent: #007aff; --bg: #0f172a; --card: #1e293b; --text: #f8fafc; }
            body { font-family: 'Inter', sans-serif; background: var(--bg); color: var(--text); margin: 0; display: flex; height: 100vh; overflow: hidden; }
            
            /* Sidebar */
            #sidebar { width: 320px; background: #111827; border-right: 1px solid #334155; display: flex; flex-direction: column; }
            .search-box { padding: 20px; border-bottom: 1px solid #334155; display:flex; justify-content: center}
            .search-box input { width: 90%; padding: 12px; border-radius: 8px; border: none; background: #1e293b; color: white; outline: none; }
            .chapter-list { flex: 1; overflow-y: auto; padding: 10px; }
            .chapter-item { padding: 12px 15px; border-radius: 8px; cursor: pointer; margin-bottom: 5px; font-size: 0.85rem; transition: 0.2s; color: #94a3b8; border: 1px solid transparent; }
            .chapter-item:hover { background: #1e293b; color: white; }
            .chapter-item.active { background: rgba(0, 122, 255, 0.1); color: var(--accent); border-color: var(--accent); }

            /* Player */
            #workspace { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; background: radial-gradient(circle at center, #1e293b 0%, #0f172a 100%); }
            .player-card { background: var(--card); width: 480px; padding: 40px; border-radius: 32px; box-shadow: 0 25px 50px -12px rgba(0,0,0,0.5); text-align: center; border: 1px solid #334155; }
            
            #display { min-height: 120px; font-size: 1.4rem; font-weight: 500; line-height: 1.6; margin-bottom: 30px; color: #f1f5f9; display: flex; align-items: center; justify-content: center; }
            
            /* Controls Area */
            .progress-area { margin-bottom: 25px; }
            .progress-bar { height: 6px; background: #334155; border-radius: 10px; position: relative; cursor: pointer; }
            #progress-fill { height: 100%; background: var(--accent); border-radius: 10px; width: 0%; transition: width 0.1s linear; }
            .time-info { display: flex; justify-content: space-between; font-size: 0.75rem; color: #64748b; margin-top: 10px; font-family: monospace; }

            .main-controls { display: flex; align-items: center; justify-content: center; gap: 25px; margin-bottom: 25px; }
            .secondary-btn { background: none; border: none; color: #94a3b8; font-size: 1.2rem; cursor: pointer; transition: 0.2s; }
            .secondary-btn:hover { color: white; }
            .play-btn { width: 72px; height: 72px; background: var(--accent); border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 1.6rem; cursor: pointer; border: none; color: white; box-shadow: 0 0 20px rgba(0,122,255,0.4); }
            
            .bottom-bar { display: flex; justify-content: space-between; align-items: center; padding-top: 20px; border-top: 1px solid #334155; }
            .vol-control { display: flex; align-items: center; gap: 10px; color: #64748b; }
            input[type="range"] { accent-color: var(--accent); cursor: pointer; }
            
            select { background: #111827; color: #94a3b8; border: 1px solid #334155; padding: 5px 10px; border-radius: 6px; font-size: 0.8rem; outline: none; }
            
            /* Loading Spinner */
            .loader { border: 3px solid #334155; border-top: 3px solid var(--accent); border-radius: 50%; width: 20px; height: 20px; animation: spin 1s linear infinite; display: inline-block; vertical-align: middle; margin-right: 10px; }
            @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
            .hidden { display: none; }
        </style>
    </head>
    <body>
        <div id="sidebar">
            <div class="search-box"><input type="text" id="q" placeholder="Jump to chapter..." onkeyup="filter()"></div>
            <div id="list" class="chapter-list"></div>
        </div>
        
        <div id="workspace">
            <div class="player-card">
                <div id="status" class="hidden" style="font-size: 0.7rem; color: var(--accent); margin-bottom: 15px;">
                    <div class="loader"></div> GENERATING AUDIO...
                </div>
                <h2 id="chTitle" style="margin: 0 0 10px; font-size: 1rem; color: #64748b; text-transform: uppercase; letter-spacing: 1px;">Select Chapter</h2>
                
                <div id="display">Tap a chapter to start listening</div>

                <div class="progress-area">
                    <div class="progress-bar" onclick="seek(event)"><div id="progress-fill"></div></div>
                    <div class="time-info"><span id="cur">0:00</span><span id="dur">0:00</span></div>
                </div>

                <div class="main-controls">
                    <button class="secondary-btn" onclick="skip(-10)"><i class="fas fa-undo"></i></button>
                    <button class="play-btn" id="playBtn" onclick="toggle()"><i class="fas fa-play"></i></button>
                    <button class="secondary-btn" onclick="skip(10)"><i class="fas fa-redo"></i></button>
                </div>

                <div class="bottom-bar">
                    <div class="vol-control">
                        <i class="fas fa-volume-up"></i>
                        <input type="range" min="0" max="1" step="0.1" value="1" oninput="audio.volume = this.value">
                    </div>
                    <div style="display: flex; gap: 10px;">
                        <select id="voice">
                        <option value="am_adam">am_adam</option>
                        <option value="af_heart">af_heart</option>
                        </select>
                        <select id="speed" onchange="audio.playbackRate = this.value">
                            <option value="1">1.0x</option>
                            <option value="1.25">1.25x</option>
                            <option value="1.5">1.5x</option>
                            <option value="2">2.0x</option>
                        </select>
                    </div>
                </div>
            </div>
        </div>

        <audio id="audio"></audio>

        <script>
            let chapters = [];
            let metadata = [];
            const audio = document.getElementById('audio');

            async function init() {
                const res = await fetch('/book/chapters');
                chapters = await res.json();
                render(chapters);
            }

            function render(items) {
                document.getElementById('list').innerHTML = items.map(c => `
                    <div class="chapter-item" id="ch-${c.id}" onclick="autoGen('${c.id}', '${c.name}')">${c.name}</div>
                `).join('');
            }

            function filter() {
                const val = document.getElementById('q').value.toLowerCase();
                render(chapters.filter(c => c.name.toLowerCase().includes(val)));
            }

            async function autoGen(id, name) {
                // UI Updates
                document.querySelectorAll('.chapter-item').forEach(el => el.classList.remove('active'));
                document.getElementById('ch-'+id).classList.add('active');
                document.getElementById('chTitle').innerText = name;
                document.getElementById('status').classList.remove('hidden');
                audio.pause();

                // 1. Get Text
                const txtRes = await fetch('/book/chapter-content/' + id);
                const txtData = await txtRes.json();

                // 2. Generate Audio
                const ttsRes = await fetch('/tts/chapter', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ text: txtData.content, voice: document.getElementById('voice').value })
                });
                const ttsData = await ttsRes.json();
                
                // 3. Play
                metadata = ttsData.metadata;
                audio.src = "data:audio/wav;base64," + ttsData.audio;
                audio.playbackRate = document.getElementById('speed').value;
                document.getElementById('status').classList.add('hidden');
                toggle(true);
            }

            function toggle(forcePlay = false) {
                const btn = document.querySelector('#playBtn i');
                if (audio.paused || forcePlay) {
                    audio.play();
                    btn.className = 'fas fa-pause';
                } else {
                    audio.pause();
                    btn.className = 'fas fa-play';
                }
            }

            function skip(amt) { audio.currentTime += amt; }

            function seek(e) {
                const percent = e.offsetX / e.currentTarget.offsetWidth;
                audio.currentTime = percent * audio.duration;
            }

            audio.ontimeupdate = () => {
                const fill = (audio.currentTime / audio.duration) * 100;
                document.getElementById('progress-fill').style.width = fill + '%';
                document.getElementById('cur').innerText = format(audio.currentTime);
                document.getElementById('dur').innerText = format(audio.duration);
                
                const cur = metadata.find(s => audio.currentTime >= s.start && audio.currentTime <= s.end);
                if (cur) document.getElementById('display').innerText = cur.text;
            };

            function format(s) {
                if (isNaN(s)) return "0:00";
                const m = Math.floor(s / 60);
                const sec = Math.floor(s % 60);
                return `${m}:${sec < 10 ? '0' : ''}${sec}`;
            }

            init();
        </script>
    </body>
    </html>
    """)