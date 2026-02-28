import os
import sqlite3
import json
import requests
import re
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask  # Key for the fix
from ebooklib import epub

router = APIRouter(prefix="/api/mobile")

# --- PATHS ---
APP_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = os.path.join(APP_ROOT.parent, "test.db")
BOOK_PATH = os.path.join(APP_ROOT, "storage", "books", "mvs-1401-2100.epub")
TEMP_AUDIO_DIR = os.path.join(APP_ROOT, "temp_audio")

@router.get("/chapters")
async def get_chapters(skip: int = Query(0, ge=0), limit: int = Query(100, le=2000)):
    if not os.path.exists(BOOK_PATH):
        return {"items": [], "total": 0}

    try:
        book = epub.read_epub(BOOK_PATH)
        
        toc_map = {}
        for item in book.get_items():
            if isinstance(item, epub.EpubNav) or item.get_type() == 9:
                for link in book.toc:
                    if isinstance(link, tuple): 
                        for sublink in link[1]:
                            toc_map[sublink.href.split('#')[0]] = sublink.title
                    elif hasattr(link, 'href'):
                        toc_map[link.href.split('#')[0]] = link.title

        all_items = []
        for item in book.get_items_of_type(9):
            content = item.get_content()
            if len(content) > 500:
                raw_id = item.get_id()
                raw_href = item.get_name()
                display_name = toc_map.get(raw_href)
                
                if not display_name:
                    name_part = raw_href.split('/')[-1]
                    name_part = re.sub(r'\.(x)?html$', '', name_part, flags=re.IGNORECASE)
                    display_name = name_part.replace('_', ' ').replace('-', ' ').title()

                all_items.append({"id": raw_id, "name": display_name})

        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT epub_item_id FROM chapters WHERE telegram_id IS NOT NULL")
            ready_ids = {row[0] for row in cursor.fetchall()}

        for item in all_items:
            item["is_ready"] = item["id"] in ready_ids

        return {
            "items": all_items[skip : skip + limit], 
            "total": len(all_items)
        }
    except Exception as e:
        print(f"EPUB Title Error: {e}")
        return {"items": [], "total": 0}


@router.get("/metadata/{epub_item_id}")
async def get_metadata(epub_item_id: str):
    """Returns the paragraph timestamps for text-sync."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT metadata_json FROM chapters WHERE epub_item_id=?", (epub_item_id,))
        row = cursor.fetchone()
    
    return json.loads(row[0]) if row and row[0] else []


@router.get("/download/{epub_item_id}")
async def download_to_mobile(epub_item_id: str):
    """Downloads audio from Telegram and streams it to mobile, then cleans up."""
    
    # 1. Fetch file ID from DB
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT telegram_id FROM chapters WHERE epub_item_id=?", (epub_item_id,))
        row = cursor.fetchone()

    if not row or not row[0]:
        raise HTTPException(status_code=404, detail="Audio not in DB")

    # 2. Get file path from Telegram
    token = os.environ.get("TELEGRAM_TOKEN")
    tg_api_res = requests.get(f"https://api.telegram.org/bot{token}/getFile?file_id={row[0]}").json()
    
    if not tg_api_res.get("ok"):
        raise HTTPException(status_code=502, detail="Telegram API error")
        
    tg_url = f"https://api.telegram.org/file/bot{token}/{tg_api_res['result']['file_path']}"

    # 3. Save locally
    os.makedirs(TEMP_AUDIO_DIR, exist_ok=True)
    temp_path = os.path.join(TEMP_AUDIO_DIR, f"{epub_item_id}.mp3")

    with requests.get(tg_url, stream=True) as r:
        with open(temp_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

    # 4. Attach the cleanup task directly to the response
    # This prevents the file from being deleted while the user is still downloading
    cleanup = BackgroundTask(os.remove, temp_path)
    
    return FileResponse(
        path=temp_path, 
        media_type="audio/mpeg",
        filename=f"{epub_item_id}.mp3",
        background=cleanup
    )