import os
import sqlite3
import json
import requests
import asyncio
import re
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from starlette.background import BackgroundTask
from bs4 import BeautifulSoup
from pydantic import BaseModel
from ebooklib import epub
from typing import Optional, List
import uuid
from datetime import datetime
import traceback
from app.services.kokoro_service import generate_full_chapter, upload_to_telegram, get_telegram_file_url

router = APIRouter(prefix="/api/admin")
APP_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = os.path.join(APP_ROOT.parent, "test.db")
ACTIVE_QUEUE = set()
TASK_QUEUE = asyncio.Queue()

class ChapterRequest(BaseModel):
    text: str
    voice: str = "af_heart"
    speed: float = 1.0
    book_title: str = "mvs-1401-2100"
    chapter_name: str = "chapter"
    epub_item_id: str = ""

def slugify(value):
    return re.sub(r'[^\w\s-]', '', value).strip().lower().replace(' ', '_')


async def background_worker():
    """Loops forever, picking up one task at a time from the queue."""
    while True:
        request = await TASK_QUEUE.get()
        ACTIVE_QUEUE.add(request.epub_item_id)
        
        try:
            # Run the heavy TTS in a thread to avoid blocking the event loop
            await asyncio.to_thread(process_single_chapter, request)
        except Exception as e:
            print(f"Worker Error: {e}")
        finally:
            ACTIVE_QUEUE.discard(request.epub_item_id)
            TASK_QUEUE.task_done()

def process_single_chapter(request: ChapterRequest):
    """Actual TTS logic executed by the worker."""
    book_slug = slugify(request.book_title)
    chapter_slug = slugify(request.chapter_name)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check if exists
    cursor.execute("SELECT telegram_id FROM chapters WHERE book_slug=? AND epub_item_id=?", 
                   (book_slug, request.epub_item_id))
    if cursor.fetchone():
        conn.close()
        return

    try:
        audio_bytes, metadata, gen_time, tg_file_id = generate_full_chapter(
            request.text, request.voice, request.speed, chapter_slug
        )
        if tg_file_id:
            cursor.execute("""
                INSERT OR REPLACE INTO chapters (book_slug, chapter_slug, epub_item_id, telegram_id, metadata_json) 
                VALUES (?, ?, ?, ?, ?)
            """, (book_slug, chapter_slug, request.epub_item_id, tg_file_id, json.dumps(metadata)))
            conn.commit()
    finally:
        conn.close()

# Start the worker task when the module loads
@router.on_event("startup")
async def startup_event():
    asyncio.create_task(background_worker())

# --- API ENDPOINTS ---

@router.post("/tts/batch")
async def post_batch_chapters(requests: List[ChapterRequest]):
    """Adds all requests to the sequential queue."""
    for req in requests:
        await TASK_QUEUE.put(req)
    return {"status": "Added to queue", "count": len(requests)}

@router.get("/books")
async def get_books():
    """Get list of all available books"""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, title, author, file_path, cover_image, added_date, total_chapters 
                FROM books 
                ORDER BY added_date DESC
            """)
            books = cursor.fetchall()
        result = []
        for book in books:
            file_exists = os.path.exists(book[3]) if book[3] else False
            
            # Count ready chapters
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT COUNT(*) FROM chapters 
                    WHERE book_id = ? AND telegram_id IS NOT NULL
                """, (book[0],))
                ready_chapters = cursor.fetchone()[0]
            
            total_chapters = book[6] or 0
            file_path = book[3] 
            file_name = os.path.basename(file_path)
            
            result.append({
                "id": book[0],
                "title": book[1],
                "author": book[2] or "Unknown Author",
                "file_path": book[3] if file_exists else None,
                "cover_image": book[4],
                "added_date": book[5],
                "total_chapters": total_chapters,
                "ready_chapters": ready_chapters,
                "is_available": file_exists,
                "progress": (ready_chapters / total_chapters * 100) if total_chapters and total_chapters > 0 else 0,
                "file_name": file_name if file_exists else None
            })
        
        print(f"📚 Found {len(result)} books in database")
        return {"books": result}
    except Exception as e:
        print(f"❌ Error fetching books: {e}")
        traceback.print_exc()
        return {"books": []}

@router.get("/book/chapters")
async def get_book_chapters_with_status(epub_filename: str = Query(..., description="Filename of the EPUB book"),
                                        book_id: str = Query(..., description="ID of the book to check status for")):
    BOOK_PATH = os.path.join(APP_ROOT, "storage", "books", epub_filename)
    if not os.path.exists(BOOK_PATH):
        raise HTTPException(status_code=404, detail="EPUB not found")

    try:
        # Read the EPUB
        book = epub.read_epub(BOOK_PATH)
        title_map = {}

        # Helper function to process table of contents
        def process_toc(toc_list):
            for item in toc_list:
                if isinstance(item, tuple):
                    if len(item) > 0 and hasattr(item[0], 'href'):
                        title_map[item[0].href.split('#')[0]] = item[0].title
                    if len(item) > 1: process_toc(item[1])
                elif hasattr(item, 'href'):
                    title_map[item.href.split('#')[0]] = item.title

        process_toc(book.toc)

        # Get the list of Cloud Ready IDs from DB
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT epub_item_id FROM chapters WHERE telegram_id IS NOT NULL AND book_id = ?", (book_id,))
        cloud_ready_ids = [row[0] for row in cursor.fetchall()]
        conn.close()

        # Build chapters with status
        chapters = []
        id_counter = 1
        for item in book.get_items_of_type(9):
            item_id = item.get_id()
            file_name = item.get_name()
            pretty_name = title_map.get(file_name)
            if not pretty_name:
                base_name = os.path.basename(file_name)
                pretty_name = os.path.splitext(base_name)[0].replace('-', ' ').replace('_', ' ').title()
            if len(item.get_content()) > 200:
                chapters.append({
                    "id_no": id_counter,
                    "id": item_id,
                    "name": pretty_name,
                    "file": file_name,
                    "status": "Cloud Ready" if item_id in cloud_ready_ids else "Pending"
                })
                id_counter += 1

        return chapters

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/book/chapter-content/")
async def get_chapter_content(item_id: str, epub_filename: str = Query(..., description="Filename of the EPUB book")):
    try:

        BOOK_PATH = os.path.join(APP_ROOT, "storage", "books", epub_filename)
        book = epub.read_epub(BOOK_PATH)
        item = book.get_item_with_id(item_id)
        soup = BeautifulSoup(item.get_content(), 'html.parser')
        clean_text = " ".join(soup.get_text(separator=' ').split())
        return {"content": clean_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

