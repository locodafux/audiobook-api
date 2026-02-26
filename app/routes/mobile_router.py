import os
import sqlite3
import json
import requests
from pathlib import Path
from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from fastapi.responses import FileResponse
from ebooklib import epub
import re

router = APIRouter(prefix="/api/mobile")

# --- PATHS (Update if your structure changes) ---
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
        
        # Create a map of file path -> display title from the Table of Contents
        toc_map = {}
        for item in book.get_items():
            if isinstance(item, epub.EpubNav) or item.get_type() == 9: # Type 9 is document
                # We attempt to find titles in the book's internal navigation structure
                for link in book.toc:
                    if isinstance(link, tuple): # Nested chapters
                        for sublink in link[1]:
                            toc_map[sublink.href.split('#')[0]] = sublink.title
                    elif hasattr(link, 'href'):
                        toc_map[link.href.split('#')[0]] = link.title

        all_items = []
        for item in book.get_items_of_type(9):
            content = item.get_content()
            if len(content) > 500: # Only count actual content pages
                raw_id = item.get_id()
                raw_href = item.get_name()
                
                # 1. Try to get title from TOC map
                # 2. Fallback: Clean the filename if TOC doesn't have it
                display_name = toc_map.get(raw_href)
                
                if not display_name:
                    name_part = raw_href.split('/')[-1]
                    name_part = re.sub(r'\.(x)?html$', '', name_part, flags=re.IGNORECASE)
                    display_name = name_part.replace('_', ' ').replace('-', ' ').title()

                all_items.append({
                    "id": raw_id,
                    "name": display_name
                })

        # Database Check for 'Ready' status
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT epub_item_id FROM chapters WHERE telegram_id IS NOT NULL")
        ready_ids = {row[0] for row in cursor.fetchall()}
        conn.close()

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
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT metadata_json FROM chapters WHERE epub_item_id=?", (epub_item_id,))
    row = cursor.fetchone()
    conn.close()
    return json.loads(row[0]) if row and row[0] else []

@router.get("/download/{epub_item_id}")
async def download_to_mobile(epub_item_id: str, background_tasks: BackgroundTasks):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_id FROM chapters WHERE epub_item_id=?", (epub_item_id,))
    row = cursor.fetchone()
    conn.close()

    if not row or not row[0]:
        raise HTTPException(status_code=404, detail="Audio not in DB")

    token = os.environ.get("TELEGRAM_TOKEN")
    res = requests.get(f"https://api.telegram.org/bot{token}/getFile?file_id={row[0]}").json()
    tg_url = f"https://api.telegram.org/file/bot{token}/{res['result']['file_path']}"

    os.makedirs(TEMP_AUDIO_DIR, exist_ok=True)
    temp_path = os.path.join(TEMP_AUDIO_DIR, f"{epub_item_id}.mp3")

    with requests.get(tg_url, stream=True) as r:
        with open(temp_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

    background_tasks.add_task(os.remove, temp_path)
    return FileResponse(path=temp_path, media_type="audio/mpeg")