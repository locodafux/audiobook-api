import os
import sqlite3
import json
import requests
import re
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from starlette.background import BackgroundTask
from ebooklib import epub
from typing import Optional, List
import uuid
from datetime import datetime
import traceback


router = APIRouter(prefix="/api/admin")
APP_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = os.path.join(APP_ROOT.parent, "test.db")

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
                "progress": (ready_chapters / total_chapters * 100) if total_chapters and total_chapters > 0 else 0
            })
        
        print(f"📚 Found {len(result)} books in database")
        return {"books": result}
    except Exception as e:
        print(f"❌ Error fetching books: {e}")
        traceback.print_exc()
        return {"books": []}