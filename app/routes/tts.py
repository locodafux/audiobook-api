import base64
import os
import re
import json
import sqlite3
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from ebooklib import epub
from bs4 import BeautifulSoup
from pathlib import Path

# Importing the services
from app.services.kokoro_service import generate_full_chapter, upload_to_telegram, get_telegram_file_url

router = APIRouter()

# --- PATH LOGIC ---
CURRENT_FILE = Path(__file__).resolve()
APP_ROOT = CURRENT_FILE.parent.parent
DB_PATH = os.path.join(APP_ROOT.parent, "test.db")
BOOK_PATH = os.path.join(APP_ROOT, "storage", "books", "mvs-1401-2100.epub")

# --- DATABASE INIT ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Updated Table: added 'epub_item_id' to link back to the EPUB file structure
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS chapters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_slug TEXT,
        chapter_slug TEXT,
        epub_item_id TEXT,    
        telegram_id TEXT,
        metadata_json TEXT,
        UNIQUE(book_slug, epub_item_id)
    )
    ''')
    
    # Migrations for existing DBs
    columns = [row[1] for row in cursor.execute("PRAGMA table_info(chapters)")]
    if "telegram_id" not in columns:
        cursor.execute("ALTER TABLE chapters ADD COLUMN telegram_id TEXT")
    if "epub_item_id" not in columns:
        cursor.execute("ALTER TABLE chapters ADD COLUMN epub_item_id TEXT")
        
    conn.commit()
    conn.close()

init_db()

def slugify(value):
    return re.sub(r'[^\w\s-]', '', value).strip().lower().replace(' ', '_')

class ChapterRequest(BaseModel):
    text: str
    voice: str = "af_heart"
    speed: float = 1.0
    book_title: str = "mvs-1401-2100"
    chapter_name: str = "chapter"
    epub_item_id: str = "" # Added to request

    
# --- API ENDPOINTS ---

@router.get("/book/status")
async def get_generation_status():
    """Returns a list of epub_item_ids that are already generated."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT epub_item_id FROM chapters WHERE telegram_id IS NOT NULL")
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]

@router.get("/book/chapters")
async def get_book_chapters():
    if not os.path.exists(BOOK_PATH):
        raise HTTPException(status_code=404, detail="EPUB not found")
    try:
        book = epub.read_epub(BOOK_PATH)
        
        # 1. Build a map of filenames to Titles from the Table of Contents
        title_map = {}
        def process_toc(toc_list):
            for item in toc_list:
                if isinstance(item, tuple):
                    if len(item) > 0 and hasattr(item[0], 'href'):
                        title_map[item[0].href.split('#')[0]] = item[0].title
                    if len(item) > 1: process_toc(item[1])
                elif hasattr(item, 'href'):
                    title_map[item.href.split('#')[0]] = item.title

        process_toc(book.toc)

        chapters = []
        # 2. Iterate through HTML items
        for item in book.get_items_of_type(9): # 9 is standard for HTML/XHTML documents
            item_id = item.get_id()
            file_name = item.get_name()
            
            # Try to find a pretty name:
            # First choice: TOC Title
            # Second choice: Internal item title
            # Third choice: Cleaned file name
            pretty_name = title_map.get(file_name)
            
            if not pretty_name:
                # Fallback: Clean up "OEBPS/chapter-1.html" to "Chapter 1"
                base_name = os.path.basename(file_name)
                pretty_name = os.path.splitext(base_name)[0].replace('-', ' ').replace('_', ' ').title()

            # Filter out tiny items (like cover images or empty pages)
            content_length = len(item.get_content())
            if content_length > 200: 
                chapters.append({
                    "id": item_id, 
                    "name": pretty_name,
                    "file": file_name
                })
                
        return chapters
    except Exception as e:
        print(f"EPUB Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/book/chapter-content/{item_id}")
async def get_chapter_content(item_id: str):
    try:
        book = epub.read_epub(BOOK_PATH)
        item = book.get_item_with_id(item_id)
        soup = BeautifulSoup(item.get_content(), 'html.parser')
        clean_text = " ".join(soup.get_text(separator=' ').split())
        return {"content": clean_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/tts/chapter")
async def post_chapter(request: ChapterRequest):
    book_slug = slugify(request.book_title)
    chapter_slug = slugify(request.chapter_name)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check cache by epub_item_id (more reliable than slugs)
    cursor.execute("SELECT telegram_id, metadata_json FROM chapters WHERE book_slug=? AND epub_item_id=?", 
                   (book_slug, request.epub_item_id))
    row = cursor.fetchone()

    if row and row[0]:
        tg_url = get_telegram_file_url(row[0])
        if tg_url:
            conn.close()
            return {"audio_url": tg_url, "metadata": json.loads(row[1]), "cached": True}

    audio_bytes, metadata, gen_time, tg_file_id = generate_full_chapter(
        request.text, request.voice, request.speed, chapter_slug
    )

    if tg_file_id:
        cursor.execute("""
            INSERT OR REPLACE INTO chapters (book_slug, chapter_slug, epub_item_id, telegram_id, metadata_json) 
            VALUES (?, ?, ?, ?, ?)
        """, (book_slug, chapter_slug, request.epub_item_id, tg_file_id, json.dumps(metadata)))
        conn.commit()
    
    conn.close()
    return {"audio": base64.b64encode(audio_bytes).decode("utf-8"), "metadata": metadata, "cached": False}
    
# --- FRONTEND UI ---

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
            #sidebar { width: 320px; background: #111827; border-right: 1px solid #334155; display: flex; flex-direction: column; }
            .search-box { padding: 20px; border-bottom: 1px solid #334155; display:flex; justify-content: center}
            .search-box input { width: 90%; padding: 12px; border-radius: 8px; border: none; background: #1e293b; color: white; outline: none; }
            .chapter-list { flex: 1; overflow-y: auto; padding: 10px; }
            .chapter-item { padding: 12px 15px; border-radius: 8px; cursor: pointer; margin-bottom: 5px; font-size: 0.85rem; transition: 0.2s; color: #94a3b8; border: 1px solid transparent; }
            .chapter-item:hover { background: #1e293b; color: white; }
            .chapter-item.active { background: rgba(0, 122, 255, 0.1); color: var(--accent); border-color: var(--accent); }
            #workspace { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; background: radial-gradient(circle at center, #1e293b 0%, #0f172a 100%); }
            .player-card { background: var(--card); width: 480px; padding: 40px; border-radius: 32px; box-shadow: 0 25px 50px -12px rgba(0,0,0,0.5); text-align: center; border: 1px solid #334155; }
            #display { min-height: 120px; font-size: 1.4rem; font-weight: 500; line-height: 1.6; margin-bottom: 30px; color: #f1f5f9; display: flex; align-items: center; justify-content: center; }
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
                        <select id="voice"><option value="am_adam">am_adam</option><option value="af_heart">af_heart</option></select>
                        <select id="speed" onchange="audio.playbackRate = this.value">
                            <option value="1">1.0x</option><option value="1.25">1.25x</option><option value="1.5">1.5x</option><option value="2">2.0x</option>
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
                document.querySelectorAll('.chapter-item').forEach(el => el.classList.remove('active'));
                const targetEl = document.getElementById('ch-'+id);
                if(targetEl) targetEl.classList.add('active');
                
                document.getElementById('chTitle').innerText = name;
                document.getElementById('status').classList.remove('hidden');
                audio.pause();

                const txtRes = await fetch('/book/chapter-content/' + id);
                const txtData = await txtRes.json();

                const ttsRes = await fetch('/tts/chapter', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ 
                        text: txtData.content, 
                        voice: document.getElementById('voice').value,
                        speed: parseFloat(document.getElementById('speed').value), // Added speed
                        book_title: "mvs-1401-2100",
                        chapter_name: name,
                        epub_item_id: id  // <--- THIS IS THE CRITICAL ADDITION
                    })
                });
                const ttsData = await ttsRes.json();
                
                metadata = ttsData.metadata || [];
                
                // Check if it was cached or freshly generated
                if (ttsData.cached) {
                    console.log("Success: Playing from Cache");
                } else {
                    console.log("Note: Fresh generation triggered");
                }

                if (ttsData.audio_url) {
                    audio.src = ttsData.audio_url;
                } else {
                    audio.src = "data:audio/mpeg;base64," + ttsData.audio;
                }

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
                
                if (metadata.length > 0) {
                    const cur = metadata.find(s => audio.currentTime >= s.start && audio.currentTime <= s.end);
                    if (cur) document.getElementById('display').innerText = cur.text;
                } else {
                    document.getElementById('display').innerText = "Playing...";
                }
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

@router.get("/admin")
async def admin_dashboard():
    return HTMLResponse("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Audiobook Admin | Control Center</title>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
        <style>
            :root { --accent: #007aff; --bg: #0f172a; --card: #1e293b; --text: #f8fafc; --success: #10b981; }
            body { font-family: 'Inter', sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 40px; }
            .container { max-width: 1150px; margin: 0 auto; }
            .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
            
            /* Search Box */
            .search-container { position: relative; margin-bottom: 20px; }
            .search-container i { position: absolute; left: 15px; top: 50%; transform: translateY(-50%); color: #64748b; }
            #chapterSearch { width: 100%; padding: 12px 15px 12px 45px; background: #111827; border: 1px solid #334155; border-radius: 12px; color: white; outline: none; transition: 0.2s; box-sizing: border-box; }
            #chapterSearch:focus { border-color: var(--accent); box-shadow: 0 0 0 2px rgba(0, 122, 255, 0.2); }

            .actions-bar { 
                margin-bottom: 20px; display: flex; gap: 12px; align-items: center; background: #111827; padding: 15px 20px; 
                border-radius: 12px; border: 1px solid #334155; position: sticky; top: 20px; z-index: 100; box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.3);
            }
            
            table { width: 100%; border-collapse: collapse; background: var(--card); border-radius: 12px; overflow: hidden; border: 1px solid #334155; }
            th { text-align: left; padding: 15px; background: #111827; color: #64748b; font-size: 0.75rem; text-transform: uppercase; }
            td { padding: 12px 15px; border-top: 1px solid #334155; font-size: 0.9rem; }
            
            input[type="checkbox"] { width: 17px; height: 17px; accent-color: var(--accent); cursor: pointer; }
            input[type="number"] { background: #1e293b; color: white; border: 1px solid #475569; padding: 6px; border-radius: 6px; width: 60px; outline: none; }
            select { background: #1e293b; color: white; border: 1px solid #475569; padding: 7px; border-radius: 6px; outline: none; }

            .status-pill { padding: 4px 10px; border-radius: 20px; font-size: 0.75rem; font-weight: bold; }
            .status-ready { background: rgba(16, 185, 129, 0.1); color: var(--success); }
            .status-missing { background: rgba(244, 63, 94, 0.1); color: #f43f5e; }
            
            .gen-btn { background: var(--accent); color: white; border: none; padding: 8px 14px; border-radius: 6px; cursor: pointer; font-weight: 500; display: inline-flex; align-items: center; gap: 6px; }
            .batch-btn { background: var(--success); }
            .range-btn { background: #475569; font-size: 0.8rem; }
            .gen-btn:disabled { background: #334155; opacity: 0.5; }
            
            .loader { border: 2px solid #f3f3f3; border-top: 2px solid var(--accent); border-radius: 50%; width: 14px; height: 14px; animation: spin 1s linear infinite; }
            @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
            tr.processing { background: rgba(0, 122, 255, 0.08); }
            .hidden { display: none !important; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Chapter Management</h1>
                <div id="stats" style="color: #94a3b8; font-family: monospace;">Syncing...</div>
            </div>

            <div class="search-container">
                <i class="fas fa-search"></i>
                <input type="text" id="chapterSearch" placeholder="Search by title or chapter number..." onkeyup="filterChapters()">
            </div>

            <div class="actions-bar">
                <input type="checkbox" id="selectAll" onclick="toggleAll(this)">
                
                <div style="display: flex; align-items: center; gap: 8px; border-left: 1px solid #334155; padding-left: 15px;">
                    <span style="font-size: 0.8rem; color: #64748b;">RANGE:</span>
                    <input type="number" id="rangeFrom" placeholder="From">
                    <span style="color: #64748b;">-</span>
                    <input type="number" id="rangeTo" placeholder="To">
                    <button class="gen-btn range-btn" onclick="selectRange()">Select</button>
                </div>

                <div style="border-left: 1px solid #334155; height: 25px; margin: 0 5px;"></div>

                <select id="batchVoice">
                    <option value="af_heart">af_heart (F)</option>
                    <option value="af_bella">af_bella (F)</option>
                    <option value="am_adam" selected>am_adam (M)</option>
                </select>

                <select id="batchSpeed">
                    <option value="1.0">1.0x</option>
                    <option value="1.2" selected>1.2x</option>
                    <option value="1.5">1.5x</option>
                </select>

                <button class="gen-btn batch-btn" id="batchBtn" onclick="generateSelected()" style="margin-left: auto;">
                    <i class="fas fa-play"></i> Batch Generate
                </button>
            </div>

            <table>
                <thead>
                    <tr>
                        <th style="width: 30px;">#</th>
                        <th style="width: 40px;">ID</th>
                        <th>Chapter Title</th>
                        <th>Status</th>
                        <th>Action</th>
                    </tr>
                </thead>
                <tbody id="chapterBody"></tbody>
            </table>
        </div>

        <script>
            let allChapters = [];
            let readyIds = [];

            async function loadAdmin() {
                const [chapRes, statusRes] = await Promise.all([
                    fetch('/book/chapters'),
                    fetch('/book/status')
                ]);
                allChapters = await chapRes.json();
                readyIds = await statusRes.json();
                renderTable(allChapters);
            }

            function renderTable(data) {
                document.getElementById('chapterBody').innerHTML = data.map((c, index) => {
                    const isReady = readyIds.includes(c.id);
                    const rowNum = index + 1; 
                    return `
                    <tr id="row-${c.id}" class="chapter-row" data-index="${rowNum}" data-title="${c.name.toLowerCase()}">
                        <td><input type="checkbox" class="chapter-check" data-id="${c.id}" data-name="${c.name}" data-idx="${rowNum}"></td>
                        <td style="font-family: monospace; color: #64748b; font-size: 0.7rem;">${rowNum}</td>
                        <td style="font-weight: 500;">${c.name}</td>
                        <td id="status-${c.id}">
                            ${isReady ? '<span class="status-pill status-ready">Cloud Ready</span>' : '<span class="status-pill status-missing">Missing</span>'}
                        </td>
                        <td>
                            <button class="gen-btn" id="btn-${c.id}" onclick="generate('${c.id}', '${c.name}')">
                                <i class="fas fa-play"></i>
                            </button>
                        </td>
                    </tr>`;
                }).join('');
                document.getElementById('stats').innerText = `TOTAL: ${allChapters.length} | READY: ${readyIds.length}`;
            }

            function filterChapters() {
                const query = document.getElementById('chapterSearch').value.toLowerCase();
                const rows = document.querySelectorAll('.chapter-row');
                
                rows.forEach(row => {
                    const title = row.getAttribute('data-title');
                    const index = row.getAttribute('data-index');
                    if (title.includes(query) || index.includes(query)) {
                        row.classList.remove('hidden');
                    } else {
                        row.classList.add('hidden');
                    }
                });
            }

            function selectRange() {
                const from = parseInt(document.getElementById('rangeFrom').value);
                const to = parseInt(document.getElementById('rangeTo').value);
                if (isNaN(from) || isNaN(to)) return alert("Enter valid row numbers");

                const checks = document.querySelectorAll('.chapter-row:not(.hidden) .chapter-check');
                checks.forEach(cb => {
                    const idx = parseInt(cb.getAttribute('data-idx'));
                    cb.checked = (idx >= from && idx <= to);
                });
            }

            function toggleAll(source) {
                const visibleCheckboxes = document.querySelectorAll('.chapter-row:not(.hidden) .chapter-check');
                visibleCheckboxes.forEach(cb => cb.checked = source.checked);
            }

            async function generateSelected() {
                const selected = Array.from(document.querySelectorAll('.chapter-check:checked'));
                if (selected.length === 0) return alert("Select chapters first");
                if (!confirm(`Generate ${selected.length} chapters?`)) return;

                const batchBtn = document.getElementById('batchBtn');
                batchBtn.disabled = true;
                
                for (const check of selected) {
                    const id = check.getAttribute('data-id');
                    const name = check.getAttribute('data-name');
                    const row = document.getElementById('row-' + id);
                    row.classList.add('processing');
                    row.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    await generate(id, name); 
                    row.classList.remove('processing');
                    check.checked = false;
                }
                batchBtn.disabled = false;
                alert("Batch complete!");
            }

            async function generate(id, name) {
                const btn = document.getElementById('btn-' + id);
                const statusTd = document.getElementById('status-' + id);
                const voice = document.getElementById('batchVoice').value;
                const speed = parseFloat(document.getElementById('batchSpeed').value);

                btn.disabled = true;
                btn.innerHTML = '<div class="loader"></div>';
                
                try {
                    const txtRes = await fetch('/book/chapter-content/' + id);
                    const txtData = await txtRes.json();
                    const ttsRes = await fetch('/tts/chapter', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ 
                            text: txtData.content, 
                            chapter_name: name,
                            epub_item_id: id,
                            voice: voice,
                            speed: speed
                        })
                    });
                    if (ttsRes.ok) {
                        statusTd.innerHTML = '<span class="status-pill status-ready">Cloud Ready</span>';
                    }
                } catch (e) {
                    statusTd.innerHTML = '<span class="status-pill status-missing">Error</span>';
                } finally { 
                    btn.disabled = false; 
                    btn.innerHTML = '<i class="fas fa-play"></i>';
                }
            }

            loadAdmin();
        </script>
    </body>
    </html>
    """)


