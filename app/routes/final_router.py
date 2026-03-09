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

router = APIRouter(prefix="/api/mobile")

# --- PATHS ---
APP_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = os.path.join(APP_ROOT.parent, "test.db")
BOOKS_STORAGE_PATH = os.path.join(APP_ROOT, "storage", "books")
TEMP_AUDIO_DIR = os.path.join(APP_ROOT, "temp_audio")

# Ensure directories exist
os.makedirs(BOOKS_STORAGE_PATH, exist_ok=True)
os.makedirs(TEMP_AUDIO_DIR, exist_ok=True)

print(f"📁 Books storage path: {BOOKS_STORAGE_PATH}")
print(f"📁 Database path: {DB_PATH}")


# --- DATABASE MIGRATION ---
def migrate_database():
    """Ensure database has all required columns with proper data types"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        # Debug: Print existing tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        print("📊 Existing tables:", tables)
        
        # Create books table if it doesn't exist
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS books (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                author TEXT,
                file_path TEXT NOT NULL UNIQUE,
                cover_image TEXT,
                added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_accessed TIMESTAMP,
                total_chapters INTEGER DEFAULT 0,
                metadata_json TEXT
            )
        """)
        print("✅ Books table ensured")
        
        # Check books table schema
        cursor.execute("PRAGMA table_info(books)")
        books_schema = cursor.fetchall()
        print("📚 Books table schema:", books_schema)
        
        # Create chapters table if it doesn't exist
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chapters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id TEXT NOT NULL,
                epub_item_id TEXT NOT NULL,
                chapter_number INTEGER,
                title TEXT,
                telegram_id TEXT,
                metadata_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(book_id, epub_item_id)
            )
        """)
        print("✅ Chapters table ensured")
        
        # Check chapters table schema
        cursor.execute("PRAGMA table_info(chapters)")
        chapters_schema = cursor.fetchall()
        print("📖 Chapters table schema:", chapters_schema)
        
        # Check if there are any existing chapters without book_id
        cursor.execute("SELECT COUNT(*) FROM chapters WHERE book_id IS NULL OR book_id = ''")
        orphaned_chapters = cursor.fetchone()[0]
        
        if orphaned_chapters > 0:
            print(f"⚠️ Found {orphaned_chapters} orphaned chapters")
            
            # Create a default book for orphaned chapters
            default_book_id = str(uuid.uuid4())
            default_book_path = os.path.join(BOOKS_STORAGE_PATH, "default_book.epub")
            
            # Check if default book file exists
            if not os.path.exists(default_book_path):
                # Try to find any EPUB file
                epub_files = list(Path(BOOKS_STORAGE_PATH).glob("*.epub"))
                if epub_files:
                    default_book_path = str(epub_files[0])
                    print(f"📖 Using existing EPUB file: {default_book_path}")
            
            # Insert default book
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO books (id, title, author, file_path, added_date, total_chapters)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    default_book_id,
                    "Default Book",
                    "Unknown",
                    default_book_path,
                    datetime.now().isoformat(),
                    0
                ))
                print(f"✅ Created default book with ID: {default_book_id}")
                
                # Update orphaned chapters
                cursor.execute("UPDATE chapters SET book_id = ? WHERE book_id IS NULL OR book_id = ''", (default_book_id,))
                print(f"✅ Linked {orphaned_chapters} chapters to default book")
                
            except Exception as e:
                print(f"❌ Error creating default book: {e}")
        
        # Create indexes
        try:
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_chapters_book_id ON chapters(book_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_chapters_telegram ON chapters(telegram_id) WHERE telegram_id IS NOT NULL")
            print("✅ Indexes created")
        except sqlite3.OperationalError as e:
            print(f"⚠️ Could not create indexes: {e}")
        
        conn.commit()
        print("✅ Database migration completed")


# Initialize database
migrate_database()


# --- TEST PAGE UI ENDPOINT ---
@router.get("/test", response_class=HTMLResponse)
async def test_page():
    """Return a beautiful test page for the book management API"""
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>📚 Book Manager - Test UI</title>
        <style>
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }

            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                padding: 20px;
            }

            .container {
                max-width: 1400px;
                margin: 0 auto;
            }

            /* Header */
            .header {
                background: white;
                border-radius: 15px;
                padding: 30px;
                margin-bottom: 30px;
                box-shadow: 0 10px 40px rgba(0,0,0,0.1);
            }

            .header h1 {
                font-size: 2.5em;
                color: #333;
                margin-bottom: 10px;
            }

            .header p {
                color: #666;
                font-size: 1.1em;
            }

            .stats {
                display: flex;
                gap: 20px;
                margin-top: 20px;
                flex-wrap: wrap;
            }

            .stat-card {
                background: #f8f9fa;
                border-radius: 10px;
                padding: 15px 25px;
                text-align: center;
                flex: 1;
                min-width: 150px;
            }

            .stat-number {
                font-size: 2em;
                font-weight: bold;
                color: #667eea;
            }

            .stat-label {
                color: #666;
                font-size: 0.9em;
            }

            /* Controls */
            .controls {
                display: flex;
                gap: 15px;
                margin-bottom: 30px;
                flex-wrap: wrap;
            }

            .btn {
                padding: 12px 24px;
                border: none;
                border-radius: 8px;
                font-size: 1em;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.3s ease;
                display: inline-flex;
                align-items: center;
                gap: 8px;
            }

            .btn-primary {
                background: #667eea;
                color: white;
            }

            .btn-primary:hover {
                background: #5a67d8;
                transform: translateY(-2px);
                box-shadow: 0 5px 15px rgba(102, 126, 234, 0.4);
            }

            .btn-success {
                background: #48bb78;
                color: white;
            }

            .btn-success:hover {
                background: #38a169;
                transform: translateY(-2px);
            }

            .btn-danger {
                background: #f56565;
                color: white;
            }

            .btn-danger:hover {
                background: #e53e3e;
                transform: translateY(-2px);
            }

            .btn-secondary {
                background: #edf2f7;
                color: #4a5568;
            }

            .btn-secondary:hover {
                background: #e2e8f0;
            }

            .btn:disabled {
                opacity: 0.5;
                cursor: not-allowed;
            }

            .btn:disabled:hover {
                transform: none;
                box-shadow: none;
            }

            /* Books Grid */
            .books-grid {
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(350px, 1fr));
                gap: 25px;
                margin-bottom: 30px;
            }

            .book-card {
                background: white;
                border-radius: 15px;
                overflow: hidden;
                box-shadow: 0 5px 20px rgba(0,0,0,0.1);
                transition: transform 0.3s ease;
            }

            .book-card:hover {
                transform: translateY(-5px);
                box-shadow: 0 8px 25px rgba(0,0,0,0.15);
            }

            .book-header {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                padding: 20px;
                position: relative;
            }

            .book-title {
                font-size: 1.3em;
                font-weight: 600;
                margin-bottom: 5px;
                padding-right: 30px;
            }

            .book-author {
                font-size: 0.9em;
                opacity: 0.9;
            }

            .book-status {
                position: absolute;
                top: 15px;
                right: 15px;
                width: 12px;
                height: 12px;
                border-radius: 50%;
            }

            .status-available {
                background: #48bb78;
                box-shadow: 0 0 10px #48bb78;
            }

            .status-unavailable {
                background: #f56565;
            }

            .book-content {
                padding: 20px;
            }

            .book-meta {
                display: flex;
                justify-content: space-between;
                margin-bottom: 15px;
                color: #666;
                font-size: 0.9em;
            }

            .progress-bar {
                width: 100%;
                height: 8px;
                background: #edf2f7;
                border-radius: 4px;
                margin: 15px 0;
                overflow: hidden;
            }

            .progress-fill {
                height: 100%;
                background: linear-gradient(90deg, #667eea, #764ba2);
                border-radius: 4px;
                transition: width 0.3s ease;
            }

            .book-actions {
                display: flex;
                gap: 10px;
                margin-top: 15px;
            }

            .book-actions .btn {
                flex: 1;
                padding: 8px 12px;
                font-size: 0.9em;
            }

            /* Modal */
            .modal {
                display: none;
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background: rgba(0,0,0,0.5);
                z-index: 1000;
                justify-content: center;
                align-items: center;
            }

            .modal.active {
                display: flex;
            }

            .modal-content {
                background: white;
                border-radius: 15px;
                padding: 30px;
                max-width: 500px;
                width: 90%;
                max-height: 80vh;
                overflow-y: auto;
            }

            .modal-header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 20px;
            }

            .modal-header h2 {
                color: #333;
            }

            .modal-close {
                background: none;
                border: none;
                font-size: 1.5em;
                cursor: pointer;
                color: #666;
            }

            .form-group {
                margin-bottom: 15px;
            }

            .form-group label {
                display: block;
                margin-bottom: 5px;
                color: #4a5568;
                font-weight: 500;
            }

            .form-group input {
                width: 100%;
                padding: 10px;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                font-size: 1em;
            }

            .form-group input:focus {
                outline: none;
                border-color: #667eea;
                box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
            }

            .form-group small {
                display: block;
                margin-top: 5px;
                color: #718096;
                font-size: 0.85em;
            }

            /* Chapters List */
            .chapters-list {
                list-style: none;
            }

            .chapter-item {
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 12px;
                border-bottom: 1px solid #edf2f7;
            }

            .chapter-item:last-child {
                border-bottom: none;
            }

            .chapter-info {
                flex: 1;
            }

            .chapter-title {
                font-weight: 500;
                color: #333;
            }

            .chapter-number {
                font-size: 0.8em;
                color: #a0aec0;
                margin-left: 5px;
            }

            .chapter-status {
                font-size: 0.8em;
                padding: 3px 8px;
                border-radius: 12px;
                background: #edf2f7;
                color: #4a5568;
                margin-left: 10px;
            }

            .chapter-status.ready {
                background: #c6f6d5;
                color: #22543d;
            }

            .chapter-actions {
                display: flex;
                gap: 8px;
            }

            .chapter-actions .btn {
                padding: 5px 10px;
                font-size: 0.8em;
            }

            /* Loading */
            .loading {
                text-align: center;
                padding: 40px;
                color: white;
            }

            .loading::after {
                content: '';
                display: inline-block;
                width: 20px;
                height: 20px;
                margin-left: 10px;
                border: 3px solid #fff;
                border-top-color: transparent;
                border-radius: 50%;
                animation: spin 0.8s linear infinite;
            }

            @keyframes spin {
                to { transform: rotate(360deg); }
            }

            /* Toast Notifications */
            .toast {
                position: fixed;
                bottom: 20px;
                right: 20px;
                background: white;
                border-radius: 8px;
                padding: 15px 20px;
                box-shadow: 0 5px 20px rgba(0,0,0,0.2);
                transform: translateX(400px);
                transition: transform 0.3s ease;
                z-index: 1001;
            }

            .toast.show {
                transform: translateX(0);
            }

            .toast.success {
                border-left: 4px solid #48bb78;
            }

            .toast.error {
                border-left: 4px solid #f56565;
            }

            .toast.info {
                border-left: 4px solid #4299e1;
            }

            /* Responsive */
            @media (max-width: 768px) {
                .header h1 {
                    font-size: 2em;
                }
                
                .books-grid {
                    grid-template-columns: 1fr;
                }
                
                .stats {
                    flex-direction: column;
                }
            }
        </style>
    </head>
    <body>
        <div class="container">
            <!-- Header -->
            <div class="header">
                <h1>📚 Book Manager</h1>
                <p>Test UI for the mobile book management API</p>
                <div class="stats" id="stats">
                    <div class="stat-card">
                        <div class="stat-number" id="totalBooks">0</div>
                        <div class="stat-label">Total Books</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-number" id="totalChapters">0</div>
                        <div class="stat-label">Total Chapters</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-number" id="readyChapters">0</div>
                        <div class="stat-label">Ready Chapters</div>
                    </div>
                </div>
            </div>

            <!-- Controls -->
            <div class="controls">
                <button class="btn btn-primary" onclick="refreshBooks()">
                    🔄 Refresh Books
                </button>
                <button class="btn btn-success" onclick="showAddBookModal()">
                    ➕ Add New Book
                </button>
                <button class="btn btn-secondary" onclick="scanFolder()">
                    📂 Scan Books Folder
                </button>
            </div>

            <!-- Books Grid -->
            <div id="booksGrid" class="books-grid">
                <!-- Books will be loaded here -->
            </div>
        </div>

        <!-- Add Book Modal -->
        <div class="modal" id="addBookModal">
            <div class="modal-content">
                <div class="modal-header">
                    <h2>Add New Book</h2>
                    <button class="modal-close" onclick="closeModal('addBookModal')">&times;</button>
                </div>
                <form onsubmit="registerBook(event)">
                    <div class="form-group">
                        <label for="bookTitle">Title *</label>
                        <input type="text" id="bookTitle" required placeholder="Enter book title">
                    </div>
                    <div class="form-group">
                        <label for="bookAuthor">Author</label>
                        <input type="text" id="bookAuthor" placeholder="Enter author name">
                    </div>
                    <div class="form-group">
                        <label for="bookFile">Filename *</label>
                        <input type="text" id="bookFile" required placeholder="book.epub">
                        <small>File must be in the books storage folder: storage/books/</small>
                    </div>
                    <div style="display: flex; gap: 10px; justify-content: flex-end;">
                        <button type="button" class="btn btn-secondary" onclick="closeModal('addBookModal')">Cancel</button>
                        <button type="submit" class="btn btn-success">Add Book</button>
                    </div>
                </form>
            </div>
        </div>

        <!-- Chapters Modal -->
        <div class="modal" id="chaptersModal">
            <div class="modal-content">
                <div class="modal-header">
                    <h2 id="chaptersBookTitle">Chapters</h2>
                    <button class="modal-close" onclick="closeModal('chaptersModal')">&times;</button>
                </div>
                <div id="chaptersList" class="chapters-list">
                    <!-- Chapters will be loaded here -->
                </div>
            </div>
        </div>

        <!-- Toast Notification -->
        <div class="toast" id="toast">
            <span id="toastMessage"></span>
        </div>

        <script>
            // API Base URL - get the current origin
            const API_BASE = window.location.origin + '/api/mobile';

            // State
            let books = [];
            let currentBookId = null;

            // Initialize
            document.addEventListener('DOMContentLoaded', () => {
                refreshBooks();
            });

            // Toast function
            function showToast(message, type = 'info') {
                const toast = document.getElementById('toast');
                const toastMessage = document.getElementById('toastMessage');
                
                toast.className = 'toast ' + type;
                toastMessage.textContent = message;
                toast.classList.add('show');
                
                setTimeout(() => {
                    toast.classList.remove('show');
                }, 3000);
            }

            // Modal functions
            function showModal(modalId) {
                document.getElementById(modalId).classList.add('active');
            }

            function closeModal(modalId) {
                document.getElementById(modalId).classList.remove('active');
            }

            // Helper function to build URLs
            function buildUrl(path) {
                return API_BASE + path;
            }

            // Refresh books
            async function refreshBooks() {
                const grid = document.getElementById('booksGrid');
                grid.innerHTML = '<div class="loading">Loading books...</div>';

                try {
                    const response = await fetch(buildUrl('/books'));
                    const data = await response.json();
                    
                    books = data.books || [];
                    renderBooks(books);
                    updateStats(books);
                    
                } catch (error) {
                    console.error('Error loading books:', error);
                    grid.innerHTML = '<div style="color: white; text-align: center;">Error loading books</div>';
                    showToast('Failed to load books: ' + error.message, 'error');
                }
            }

            // Render books
            function renderBooks(books) {
                const grid = document.getElementById('booksGrid');
                
                if (books.length === 0) {
                    grid.innerHTML = '<div style="color: white; text-align: center; grid-column: 1/-1;">No books found. Add your first book!</div>';
                    return;
                }

                grid.innerHTML = books.map(book => `
                    <div class="book-card">
                        <div class="book-header">
                            <div class="book-title">${escapeHtml(book.title)}</div>
                            <div class="book-author">${escapeHtml(book.author || 'Unknown Author')}</div>
                            <div class="book-status ${book.is_available ? 'status-available' : 'status-unavailable'}"></div>
                        </div>
                        <div class="book-content">
                            <div class="book-meta">
                                <span>📅 Added: ${new Date(book.added_date).toLocaleDateString()}</span>
                                <span>📖 ${book.total_chapters || 0} chapters</span>
                            </div>
                            <div class="progress-bar">
                                <div class="progress-fill" style="width: ${book.progress || 0}%"></div>
                            </div>
                            <div style="display: flex; justify-content: space-between; margin-bottom: 15px;">
                                <span>✅ ${book.ready_chapters || 0} ready</span>
                                <span>${Math.round(book.progress || 0)}% complete</span>
                            </div>
                            <div class="book-actions">
                                <button class="btn btn-primary" onclick="viewChapters('${book.id}')" ${!book.is_available ? 'disabled' : ''}>
                                    📖 View Chapters
                                </button>
                                <button class="btn btn-secondary" onclick="refreshBook('${book.id}')">
                                    🔄 Refresh
                                </button>
                                <button class="btn btn-danger" onclick="deleteBook('${book.id}')">
                                    🗑️ Delete
                                </button>
                            </div>
                        </div>
                    </div>
                `).join('');
            }

            // Update stats
            function updateStats(books) {
                const totalBooks = books.length;
                const totalChapters = books.reduce((sum, book) => sum + (book.total_chapters || 0), 0);
                const readyChapters = books.reduce((sum, book) => sum + (book.ready_chapters || 0), 0);
                
                document.getElementById('totalBooks').textContent = totalBooks;
                document.getElementById('totalChapters').textContent = totalChapters;
                document.getElementById('readyChapters').textContent = readyChapters;
            }

            // View chapters
            async function viewChapters(bookId) {
                currentBookId = bookId;
                const book = books.find(b => b.id === bookId);
                
                if (!book) return;
                
                document.getElementById('chaptersBookTitle').textContent = `${book.title} - Chapters`;
                document.getElementById('chaptersList').innerHTML = '<div class="loading">Loading chapters...</div>';
                
                showModal('chaptersModal');

                try {
                    const response = await fetch(buildUrl(`/books/${bookId}/chapters?limit=1000`));
                    const data = await response.json();
                    
                    renderChapters(data.items || []);
                    
                } catch (error) {
                    console.error('Error loading chapters:', error);
                    document.getElementById('chaptersList').innerHTML = '<div style="color: #f56565; padding: 20px; text-align: center;">Error loading chapters</div>';
                    showToast('Failed to load chapters: ' + error.message, 'error');
                }
            }

            // Render chapters
            function renderChapters(chapters) {
                const list = document.getElementById('chaptersList');
                
                if (chapters.length === 0) {
                    list.innerHTML = '<div style="padding: 20px; text-align: center; color: #666;">No chapters found</div>';
                    return;
                }

                list.innerHTML = chapters.map(chapter => `
                    <div class="chapter-item">
                        <div class="chapter-info">
                            <span class="chapter-title">${escapeHtml(chapter.name)}</span>
                            <span class="chapter-number">#${chapter.chapter_number || ''}</span>
                            <span class="chapter-status ${chapter.is_ready ? 'ready' : ''}">
                                ${chapter.is_ready ? '✅ Ready' : '⏳ Processing'}
                            </span>
                        </div>
                        <div class="chapter-actions">
                            ${chapter.is_ready ? `
                                <button class="btn btn-primary" onclick="downloadChapter('${currentBookId}', '${chapter.id}')">
                                    ⬇️ Download
                                </button>
                                <button class="btn btn-secondary" onclick="viewMetadata('${currentBookId}', '${chapter.id}')">
                                    📊 Metadata
                                </button>
                            ` : ''}
                        </div>
                    </div>
                `).join('');
            }

            // Download chapter
            function downloadChapter(bookId, chapterId) {
                try {
                    showToast('Starting download...', 'info');
                    
                    // Open in new tab to trigger download
                    window.open(buildUrl(`/download/${bookId}/${chapterId}`), '_blank');
                    
                } catch (error) {
                    console.error('Error downloading chapter:', error);
                    showToast('Failed to download chapter: ' + error.message, 'error');
                }
            }

            // View metadata
            async function viewMetadata(bookId, chapterId) {
                try {
                    const response = await fetch(buildUrl(`/metadata/${bookId}/${chapterId}`));
                    const metadata = await response.json();
                    
                    alert(JSON.stringify(metadata, null, 2));
                    
                } catch (error) {
                    console.error('Error loading metadata:', error);
                    showToast('Failed to load metadata: ' + error.message, 'error');
                }
            }

            // Add book modal
            function showAddBookModal() {
                document.getElementById('bookTitle').value = '';
                document.getElementById('bookAuthor').value = '';
                document.getElementById('bookFile').value = '';
                showModal('addBookModal');
            }

            // Register book
            async function registerBook(event) {
                event.preventDefault();
                
                const title = document.getElementById('bookTitle').value;
                const author = document.getElementById('bookAuthor').value;
                const filename = document.getElementById('bookFile').value;

                try {
                    // Build URL with query parameters properly
                    let url = buildUrl('/books/register');
                    url += `?title=${encodeURIComponent(title)}`;
                    if (author) {
                        url += `&author=${encodeURIComponent(author)}`;
                    }
                    url += `&filename=${encodeURIComponent(filename)}`;

                    console.log('Registering book with URL:', url);
                    
                    const response = await fetch(url, { 
                        method: 'POST',
                        headers: {
                            'Accept': 'application/json'
                        }
                    });
                    
                    if (!response.ok) {
                        const error = await response.json();
                        throw new Error(error.detail || 'Failed to register book');
                    }
                    
                    const result = await response.json();
                    console.log('Registration result:', result);
                    
                    closeModal('addBookModal');
                    showToast('Book registered successfully!', 'success');
                    refreshBooks();
                    
                } catch (error) {
                    console.error('Error registering book:', error);
                    showToast(error.message, 'error');
                }
            }

            // Refresh book
            async function refreshBook(bookId) {
                try {
                    showToast('Refreshing book metadata...', 'info');
                    
                    const response = await fetch(buildUrl(`/books/${bookId}/refresh`), { 
                        method: 'POST',
                        headers: {
                            'Accept': 'application/json'
                        }
                    });
                    
                    if (!response.ok) {
                        throw new Error('Failed to refresh book');
                    }
                    
                    showToast('Book refreshed successfully!', 'success');
                    refreshBooks();
                    
                } catch (error) {
                    console.error('Error refreshing book:', error);
                    showToast(error.message, 'error');
                }
            }

            // Delete book
            async function deleteBook(bookId) {
                if (!confirm('Are you sure you want to delete this book? This action cannot be undone.')) {
                    return;
                }

                try {
                    const response = await fetch(buildUrl(`/books/${bookId}?delete_file=false`), { 
                        method: 'DELETE',
                        headers: {
                            'Accept': 'application/json'
                        }
                    });
                    
                    if (!response.ok) {
                        throw new Error('Failed to delete book');
                    }
                    
                    showToast('Book deleted successfully!', 'success');
                    refreshBooks();
                    
                } catch (error) {
                    console.error('Error deleting book:', error);
                    showToast(error.message, 'error');
                }
            }

            // Scan folder
            async function scanFolder() {
                try {
                    showToast('Scanning books folder...', 'info');
                    
                    const response = await fetch(buildUrl('/scan-books-folder'), { 
                        method: 'POST',
                        headers: {
                            'Accept': 'application/json'
                        }
                    });
                    const result = await response.json();
                    
                    console.log('Scan result:', result);
                    showToast(`Scan complete: ${result.registered} registered, ${result.skipped} skipped`, 'success');
                    refreshBooks();
                    
                } catch (error) {
                    console.error('Error scanning folder:', error);
                    showToast(error.message, 'error');
                }
            }

            // Escape HTML to prevent XSS
            function escapeHtml(unsafe) {
                if (!unsafe) return '';
                return unsafe
                    .replace(/&/g, "&amp;")
                    .replace(/</g, "&lt;")
                    .replace(/>/g, "&gt;")
                    .replace(/"/g, "&quot;")
                    .replace(/'/g, "&#039;");
            }
        </script>
    </body>
    </html>
    """


# --- BOOK MANAGEMENT API ENDPOINTS ---

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
            # Check if file still exists
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


@router.post("/books/register")
async def register_book(
    title: str = Query(...),
    author: Optional[str] = Query(None),
    filename: str = Query(...)
):
    """
    Register a new book in the database without uploading the file.
    The file should already be in the books storage folder.
    """
    print(f"\n📝 Attempting to register book:")
    print(f"  Title: {title}")
    print(f"  Author: {author}")
    print(f"  Filename: {filename}")
    
    try:
        # Validate file exists
        file_path = os.path.join(BOOKS_STORAGE_PATH, filename)
        print(f"  Full path: {file_path}")
        print(f"  File exists: {os.path.exists(file_path)}")
        
        if not os.path.exists(file_path):
            # List all files in the books folder
            print(f"  Files in {BOOKS_STORAGE_PATH}:")
            if os.path.exists(BOOKS_STORAGE_PATH):
                for f in os.listdir(BOOKS_STORAGE_PATH):
                    print(f"    - {f}")
            else:
                print(f"    Directory does not exist!")
            raise HTTPException(status_code=404, detail=f"Book file not found: {filename}")
        
        # Check if file extension is supported
        if not filename.lower().endswith('.epub'):
            raise HTTPException(status_code=400, detail="Only EPUB files are supported")
        
        # Generate unique ID
        book_id = str(uuid.uuid4())
        print(f"  Generated book ID: {book_id}")
        
        # Insert into database
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            
            # Check if file already registered
            cursor.execute("SELECT id FROM books WHERE file_path = ?", (file_path,))
            existing = cursor.fetchone()
            if existing:
                print(f"  Book already registered with ID: {existing[0]}")
                raise HTTPException(status_code=400, detail="Book already registered")
            
            # Insert the book
            now = datetime.now().isoformat()
            print(f"  Inserting into database at {now}")
            
            # First, check the actual columns in the books table
            cursor.execute("PRAGMA table_info(books)")
            columns = [col[1] for col in cursor.fetchall()]
            print(f"  Books table columns: {columns}")
            
            # Prepare insert statement based on available columns
            insert_columns = ['id', 'title', 'file_path', 'added_date']
            insert_values = [book_id, title, file_path, now]
            
            if 'author' in columns and author:
                insert_columns.append('author')
                insert_values.append(author)
            
            if 'total_chapters' in columns:
                insert_columns.append('total_chapters')
                insert_values.append(0)
            
            # Build and execute the insert statement
            placeholders = ','.join(['?' for _ in insert_columns])
            columns_str = ','.join(insert_columns)
            
            insert_sql = f"INSERT INTO books ({columns_str}) VALUES ({placeholders})"
            print(f"  SQL: {insert_sql}")
            print(f"  Values: {insert_values}")
            
            cursor.execute(insert_sql, insert_values)
            conn.commit()
            print(f"  ✅ Book inserted successfully")
        
        # Try to extract metadata and chapter count
        try:
            print(f"  🔄 Refreshing metadata...")
            await refresh_book_metadata(book_id)
        except Exception as e:
            print(f"  ⚠️ Warning: Could not extract metadata: {e}")
            traceback.print_exc()
        
        return {
            "message": "Book registered successfully",
            "book_id": book_id,
            "file_path": file_path
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error registering book: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/books/{book_id}")
async def delete_book(book_id: str, delete_file: bool = False):
    """Delete a book from the database and optionally remove the file"""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            
            # Get file path before deletion
            cursor.execute("SELECT file_path FROM books WHERE id = ?", (book_id,))
            book = cursor.fetchone()
            
            if not book:
                raise HTTPException(status_code=404, detail="Book not found")
            
            file_path = book[0]
            
            # Delete the book (chapters will be deleted due to foreign key CASCADE)
            cursor.execute("DELETE FROM books WHERE id = ?", (book_id,))
            conn.commit()
        
        # Optionally delete the file
        if delete_file and file_path and os.path.exists(file_path):
            os.remove(file_path)
            file_deleted = True
        else:
            file_deleted = False
        
        return {
            "message": "Book deleted successfully",
            "file_deleted": file_deleted
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/books/{book_id}/refresh")
async def refresh_book_metadata(book_id: str):
    """Refresh book metadata and chapter list from the EPUB file"""
    try:
        # Get book info
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT file_path, title FROM books WHERE id = ?", (book_id,))
            book = cursor.fetchone()
        
        if not book:
            raise HTTPException(status_code=404, detail="Book not found")
        
        file_path, title = book
        print(f"  Refreshing book: {title}")
        print(f"  File path: {file_path}")
        
        if not os.path.exists(file_path):
            print(f"  ❌ Book file not found: {file_path}")
            raise HTTPException(status_code=404, detail="Book file not found")
        
        # Read EPUB
        try:
            book_epub = epub.read_epub(file_path)
            print(f"  ✅ Successfully read EPUB file")
        except Exception as e:
            print(f"  ❌ Error reading EPUB: {e}")
            raise HTTPException(status_code=500, detail=f"Error reading EPUB: {str(e)}")
        
        # Extract chapters
        chapters = []
        chapter_number = 1
        
        # Build TOC map
        toc_map = {}
        for item in book_epub.get_items():
            if isinstance(item, epub.EpubNav) or item.get_type() == 9:
                for link in book_epub.toc:
                    if isinstance(link, tuple): 
                        for sublink in link[1]:
                            if hasattr(sublink, 'href'):
                                toc_map[sublink.href.split('#')[0]] = sublink.title
                    elif hasattr(link, 'href'):
                        toc_map[link.href.split('#')[0]] = link.title
        
        # Get all HTML items
        for item in book_epub.get_items_of_type(9):
            content = item.get_content()
            if len(content) > 500:  # Skip very small files (likely not chapters)
                raw_id = item.get_id()
                raw_href = item.get_name()
                display_name = toc_map.get(raw_href)
                
                if not display_name:
                    name_part = raw_href.split('/')[-1]
                    name_part = re.sub(r'\.(x)?html$', '', name_part, flags=re.IGNORECASE)
                    display_name = name_part.replace('_', ' ').replace('-', ' ').title()
                
                chapters.append({
                    "epub_item_id": raw_id,
                    "chapter_number": chapter_number,
                    "title": display_name
                })
                chapter_number += 1
        
        print(f"  Found {len(chapters)} chapters")
        
        # Update database
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            
            # Update total chapters
            cursor.execute("""
                UPDATE books 
                SET total_chapters = ?, last_accessed = ? 
                WHERE id = ?
            """, (len(chapters), datetime.now().isoformat(), book_id))
            
            # Insert or update chapters
            for chapter in chapters:
                try:
                    cursor.execute("""
                        INSERT OR IGNORE INTO chapters (book_id, epub_item_id, chapter_number, title)
                        VALUES (?, ?, ?, ?)
                    """, (book_id, chapter["epub_item_id"], chapter["chapter_number"], chapter["title"]))
                except Exception as e:
                    print(f"  Error inserting chapter {chapter['title']}: {e}")
            
            conn.commit()
            print(f"  ✅ Database updated with {len(chapters)} chapters")
        
        return {
            "message": "Book metadata refreshed",
            "book_id": book_id,
            "total_chapters": len(chapters)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error refreshing book: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# --- UPDATED EXISTING ENDPOINTS WITH BOOK SUPPORT ---

@router.get("/books/{book_id}/chapters")
async def get_book_chapters(
    book_id: str,
    skip: int = Query(0, ge=0), 
    limit: int = Query(100, le=2000)
):
    """Get chapters for a specific book"""
    
    # Verify book exists
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM books WHERE id = ?", (book_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Book not found")
    
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            
            # Get chapters with ready status
            cursor.execute("""
                SELECT 
                    c.epub_item_id,
                    c.title,
                    c.chapter_number,
                    CASE WHEN c.telegram_id IS NOT NULL THEN 1 ELSE 0 END as is_ready
                FROM chapters c
                WHERE c.book_id = ?
                ORDER BY c.chapter_number
                LIMIT ? OFFSET ?
            """, (book_id, limit, skip))
            
            chapters = cursor.fetchall()
            
            # Get total count
            cursor.execute("SELECT COUNT(*) FROM chapters WHERE book_id = ?", (book_id,))
            total = cursor.fetchone()[0]
        
        items = [
            {
                "id": row[0],
                "name": row[1],
                "chapter_number": row[2],
                "is_ready": bool(row[3])
            }
            for row in chapters
        ]
        
        return {
            "book_id": book_id,
            "items": items,
            "total": total,
            "skip": skip,
            "limit": limit
        }
        
    except Exception as e:
        print(f"Error fetching chapters: {e}")
        return {"items": [], "total": 0, "book_id": book_id}


@router.get("/metadata/{book_id}/{epub_item_id}")
async def get_chapter_metadata(book_id: str, epub_item_id: str):
    """Returns the paragraph timestamps for text-sync for a specific chapter."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT metadata_json FROM chapters 
            WHERE book_id = ? AND epub_item_id = ?
        """, (book_id, epub_item_id))
        row = cursor.fetchone()
    
    if not row:
        raise HTTPException(status_code=404, detail="Chapter not found")
    
    return json.loads(row[0]) if row[0] else []


@router.get("/download/{book_id}/{epub_item_id}")
async def download_chapter_audio(book_id: str, epub_item_id: str):
    """Downloads audio from Telegram and streams it to mobile, then cleans up."""
    
    # 1. Verify book and chapter exist and get telegram_id
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT telegram_id, title FROM chapters 
            WHERE book_id = ? AND epub_item_id = ?
        """, (book_id, epub_item_id))
        row = cursor.fetchone()

    if not row or not row[0]:
        raise HTTPException(status_code=404, detail="Audio not found in database")

    telegram_id, chapter_title = row

    # 2. Get file path from Telegram
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        raise HTTPException(status_code=500, detail="Telegram token not configured")
    
    tg_api_res = requests.get(f"https://api.telegram.org/bot{token}/getFile?file_id={telegram_id}").json()
    
    if not tg_api_res.get("ok"):
        raise HTTPException(status_code=502, detail=f"Telegram API error: {tg_api_res.get('description', 'Unknown error')}")
    
    if 'result' not in tg_api_res or 'file_path' not in tg_api_res['result']:
        raise HTTPException(status_code=502, detail="Invalid Telegram API response")
        
    tg_url = f"https://api.telegram.org/file/bot{token}/{tg_api_res['result']['file_path']}"

    # 3. Save locally with book-specific naming to avoid collisions
    os.makedirs(TEMP_AUDIO_DIR, exist_ok=True)
    temp_filename = f"{book_id}_{epub_item_id}.mp3"
    temp_path = os.path.join(TEMP_AUDIO_DIR, temp_filename)

    # Download with progress tracking (optional)
    try:
        with requests.get(tg_url, stream=True, timeout=30) as r:
            r.raise_for_status()
            total_size = int(r.headers.get('content-length', 0))
            
            with open(temp_path, "wb") as f:
                downloaded = 0
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        # Could add progress logging here if needed
                        
    except requests.RequestException as e:
        # Clean up partial download if any
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise HTTPException(status_code=502, detail=f"Download failed: {str(e)}")

    # 4. Update last accessed timestamp for book
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE books SET last_accessed = ? WHERE id = ?", (datetime.now().isoformat(), book_id))
        conn.commit()

    # 5. Attach the cleanup task
    cleanup = BackgroundTask(os.remove, temp_path)
    
    # Generate a friendly filename
    safe_title = re.sub(r'[^\w\s-]', '', chapter_title or epub_item_id)
    safe_title = re.sub(r'[-\s]+', '-', safe_title)
    filename = f"{safe_title}.mp3" if safe_title else f"{epub_item_id}.mp3"
    
    return FileResponse(
        path=temp_path, 
        media_type="audio/mpeg",
        filename=filename,
        background=cleanup
    )


@router.get("/book-info/{book_id}")
async def get_book_info(book_id: str):
    """Get detailed information about a specific book"""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, title, author, file_path, cover_image, added_date, last_accessed, total_chapters
                FROM books 
                WHERE id = ?
            """, (book_id,))
            book = cursor.fetchone()
        
        if not book:
            raise HTTPException(status_code=404, detail="Book not found")
        
        # Check if file exists
        file_exists = os.path.exists(book[3]) if book[3] else False
        
        # Count ready chapters
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) FROM chapters 
                WHERE book_id = ? AND telegram_id IS NOT NULL
            """, (book_id,))
            ready_chapters = cursor.fetchone()[0]
        
        total_chapters = book[7] or 0
        
        return {
            "id": book[0],
            "title": book[1],
            "author": book[2] or "Unknown Author",
            "file_path": book[3] if file_exists else None,
            "cover_image": book[4],
            "added_date": book[5],
            "last_accessed": book[6],
            "total_chapters": total_chapters,
            "ready_chapters": ready_chapters,
            "is_available": file_exists,
            "progress": (ready_chapters / total_chapters * 100) if total_chapters and total_chapters > 0 else 0
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- UTILITY ENDPOINTS ---

@router.post("/scan-books-folder")
async def scan_books_folder():
    """Scan the books folder for EPUB files and register any new ones"""
    print("\n🔍 Scanning books folder...")
    try:
        if not os.path.exists(BOOKS_STORAGE_PATH):
            print(f"  ❌ Books folder does not exist: {BOOKS_STORAGE_PATH}")
            return {
                "message": "Scan completed",
                "registered": 0,
                "skipped": 0,
                "errors": ["Books folder does not exist"]
            }
        
        epub_files = list(Path(BOOKS_STORAGE_PATH).glob("*.epub"))
        print(f"  Found {len(epub_files)} EPUB files:")
        for f in epub_files:
            print(f"    - {f.name}")
        
        registered_count = 0
        skipped_count = 0
        errors = []
        
        for epub_file in epub_files:
            try:
                print(f"\n  Processing: {epub_file.name}")
                
                # Check if already registered
                with sqlite3.connect(DB_PATH) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT id FROM books WHERE file_path = ?", (str(epub_file),))
                    existing = cursor.fetchone()
                    
                    if existing:
                        print(f"    ⏭️ Already registered with ID: {existing[0]}")
                        skipped_count += 1
                        continue
                
                # Try to extract title and author from EPUB
                try:
                    book = epub.read_epub(str(epub_file))
                    title_meta = book.get_metadata('DC', 'title')
                    title = title_meta[0][0] if title_meta else epub_file.stem
                    
                    author_meta = book.get_metadata('DC', 'creator')
                    author = author_meta[0][0] if author_meta else None
                    
                    print(f"    Extracted title: {title}")
                    print(f"    Extracted author: {author}")
                except Exception as e:
                    # Fallback to filename
                    title = epub_file.stem.replace('_', ' ').replace('-', ' ').title()
                    author = None
                    print(f"    Using filename as title: {title}")
                    print(f"    Error reading metadata: {e}")
                
                # Register the book
                book_id = str(uuid.uuid4())
                now = datetime.now().isoformat()
                
                with sqlite3.connect(DB_PATH) as conn:
                    cursor = conn.cursor()
                    
                    # Check actual columns
                    cursor.execute("PRAGMA table_info(books)")
                    columns = [col[1] for col in cursor.fetchall()]
                    
                    insert_columns = ['id', 'title', 'file_path', 'added_date']
                    insert_values = [book_id, title, str(epub_file), now]
                    
                    if 'author' in columns and author:
                        insert_columns.append('author')
                        insert_values.append(author)
                    
                    if 'total_chapters' in columns:
                        insert_columns.append('total_chapters')
                        insert_values.append(0)
                    
                    placeholders = ','.join(['?' for _ in insert_columns])
                    columns_str = ','.join(insert_columns)
                    
                    insert_sql = f"INSERT INTO books ({columns_str}) VALUES ({placeholders})"
                    cursor.execute(insert_sql, insert_values)
                    conn.commit()
                
                print(f"    ✅ Registered with ID: {book_id}")
                
                # Refresh metadata
                try:
                    await refresh_book_metadata(book_id)
                except Exception as e:
                    print(f"    ⚠️ Warning: Could not refresh metadata: {e}")
                
                registered_count += 1
                
            except Exception as e:
                errors.append(f"{epub_file.name}: {str(e)}")
                print(f"    ❌ Error: {e}")
                traceback.print_exc()
        
        print(f"\n✅ Scan complete: {registered_count} registered, {skipped_count} skipped")
        if errors:
            print(f"❌ Errors: {errors}")
        
        return {
            "message": "Scan completed",
            "registered": registered_count,
            "skipped": skipped_count,
            "errors": errors
        }
        
    except Exception as e:
        print(f"❌ Error scanning folder: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# Keep the original endpoints for backward compatibility if needed
@router.get("/chapters")
async def get_chapters_legacy(skip: int = Query(0, ge=0), limit: int = Query(100, le=2000)):
    """Legacy endpoint - returns chapters from first available book"""
    # Get first book
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM books LIMIT 1")
        book = cursor.fetchone()
    
    if book:
        return await get_book_chapters(book[0], skip, limit)
    
    return {"items": [], "total": 0}


@router.get("/metadata/{epub_item_id}")
async def get_metadata_legacy(epub_item_id: str):
    """Legacy endpoint - returns metadata from first available book"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT book_id FROM chapters WHERE epub_item_id = ? LIMIT 1", (epub_item_id,))
        chapter = cursor.fetchone()
    
    if chapter:
        return await get_chapter_metadata(chapter[0], epub_item_id)
    
    return []

@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard():
    """Dynamic admin dashboard supporting multiple books"""
    return HTMLResponse("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>📚 Multi-Book Audiobook Admin</title>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
        <style>
            :root { 
                --accent: #007aff; 
                --bg: #0f172a; 
                --card: #1e293b; 
                --text: #f8fafc; 
                --success: #10b981;
                --warning: #f59e0b;
                --danger: #ef4444;
                --sidebar-width: 280px;
            }
            
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }

            body { 
                font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; 
                background: var(--bg); 
                color: var(--text); 
                margin: 0; 
                display: flex; 
                height: 100vh; 
                overflow: hidden; 
            }

            /* Sidebar */
            #sidebar { 
                width: var(--sidebar-width); 
                background: #111827; 
                border-right: 1px solid #334155; 
                display: flex; 
                flex-direction: column;
                transition: width 0.3s ease;
            }

            #sidebar.collapsed {
                width: 60px;
            }

            .sidebar-header {
                padding: 20px;
                border-bottom: 1px solid #334155;
                display: flex;
                align-items: center;
                justify-content: space-between;
            }

            .sidebar-header h3 {
                font-size: 1rem;
                color: #94a3b8;
                text-transform: uppercase;
                letter-spacing: 1px;
            }

            .sidebar-toggle {
                background: none;
                border: none;
                color: #64748b;
                cursor: pointer;
                font-size: 1.2rem;
                transition: color 0.2s;
            }

            .sidebar-toggle:hover {
                color: var(--accent);
            }

            .book-search {
                padding: 15px;
                border-bottom: 1px solid #334155;
            }

            .book-search input {
                width: 100%;
                padding: 10px 12px;
                background: #1e293b;
                border: 1px solid #334155;
                border-radius: 8px;
                color: white;
                outline: none;
                font-size: 0.9rem;
            }

            .book-search input:focus {
                border-color: var(--accent);
                box-shadow: 0 0 0 2px rgba(0, 122, 255, 0.2);
            }

            .book-list {
                flex: 1;
                overflow-y: auto;
                padding: 10px;
            }

            .book-item {
                padding: 12px 15px;
                border-radius: 8px;
                cursor: pointer;
                margin-bottom: 5px;
                transition: all 0.2s;
                border: 1px solid transparent;
                display: flex;
                align-items: center;
                gap: 10px;
            }

            .book-item:hover {
                background: #1e293b;
            }

            .book-item.active {
                background: rgba(0, 122, 255, 0.1);
                border-color: var(--accent);
            }

            .book-item.active .book-title {
                color: var(--accent);
            }

            .book-icon {
                width: 32px;
                height: 32px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                border-radius: 8px;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 0.9rem;
                color: white;
                flex-shrink: 0;
            }

            .book-info {
                flex: 1;
                min-width: 0;
            }

            .book-title {
                font-size: 0.9rem;
                font-weight: 500;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }

            .book-meta {
                font-size: 0.7rem;
                color: #64748b;
                display: flex;
                gap: 8px;
                margin-top: 2px;
            }

            .book-badge {
                background: #1e293b;
                padding: 2px 6px;
                border-radius: 4px;
            }

            .book-badge.ready {
                background: rgba(16, 185, 129, 0.2);
                color: var(--success);
            }

            /* Main Content */
            #main-content {
                flex: 1;
                display: flex;
                flex-direction: column;
                overflow: hidden;
            }

            .main-header {
                background: #111827;
                border-bottom: 1px solid #334155;
                padding: 15px 25px;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }

            .book-title-header {
                display: flex;
                align-items: center;
                gap: 15px;
            }

            .book-title-header h1 {
                font-size: 1.5rem;
                font-weight: 600;
            }

            .book-status-badge {
                background: #1e293b;
                padding: 4px 10px;
                border-radius: 20px;
                font-size: 0.8rem;
                color: #94a3b8;
            }

            .header-actions {
                display: flex;
                gap: 10px;
            }

            /* Workspace */
            #workspace {
                flex: 1;
                overflow-y: auto;
                padding: 25px;
            }

            /* Stats Cards */
            .stats-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 20px;
                margin-bottom: 25px;
            }

            .stat-card {
                background: var(--card);
                border: 1px solid #334155;
                border-radius: 12px;
                padding: 20px;
            }

            .stat-label {
                color: #94a3b8;
                font-size: 0.8rem;
                text-transform: uppercase;
                letter-spacing: 0.5px;
                margin-bottom: 8px;
            }

            .stat-value {
                font-size: 2rem;
                font-weight: 600;
                color: white;
            }

            .stat-sub {
                color: #64748b;
                font-size: 0.8rem;
                margin-top: 5px;
            }

            /* Controls */
            .controls-bar {
                background: var(--card);
                border: 1px solid #334155;
                border-radius: 12px;
                padding: 20px;
                margin-bottom: 25px;
                display: flex;
                flex-wrap: wrap;
                gap: 15px;
                align-items: center;
                position: sticky;
                top: 0;
                z-index: 10;
            }

            .btn {
                padding: 10px 18px;
                border: none;
                border-radius: 8px;
                font-size: 0.9rem;
                font-weight: 500;
                cursor: pointer;
                transition: all 0.2s;
                display: inline-flex;
                align-items: center;
                gap: 8px;
            }

            .btn-primary {
                background: var(--accent);
                color: white;
            }

            .btn-primary:hover {
                background: #0051b3;
                transform: translateY(-1px);
            }

            .btn-success {
                background: var(--success);
                color: white;
            }

            .btn-success:hover {
                background: #0f924e;
                transform: translateY(-1px);
            }

            .btn-warning {
                background: var(--warning);
                color: white;
            }

            .btn-warning:hover {
                background: #d97706;
                transform: translateY(-1px);
            }

            .btn-danger {
                background: var(--danger);
                color: white;
            }

            .btn-danger:hover {
                background: #dc2626;
                transform: translateY(-1px);
            }

            .btn-secondary {
                background: #334155;
                color: white;
            }

            .btn-secondary:hover {
                background: #475569;
            }

            .btn:disabled {
                opacity: 0.5;
                cursor: not-allowed;
                transform: none;
            }

            .search-box {
                flex: 1;
                min-width: 200px;
                position: relative;
            }

            .search-box i {
                position: absolute;
                left: 12px;
                top: 50%;
                transform: translateY(-50%);
                color: #64748b;
            }

            .search-box input {
                width: 100%;
                padding: 10px 15px 10px 40px;
                background: #1e293b;
                border: 1px solid #334155;
                border-radius: 8px;
                color: white;
                outline: none;
            }

            .search-box input:focus {
                border-color: var(--accent);
            }

            .filter-select {
                background: #1e293b;
                color: white;
                border: 1px solid #334155;
                padding: 10px;
                border-radius: 8px;
                outline: none;
                min-width: 120px;
            }

            /* Table */
            .table-container {
                background: var(--card);
                border: 1px solid #334155;
                border-radius: 12px;
                overflow: auto;
            }

            table {
                width: 100%;
                border-collapse: collapse;
            }

            th {
                text-align: left;
                padding: 15px;
                background: #111827;
                color: #94a3b8;
                font-size: 0.8rem;
                font-weight: 600;
                text-transform: uppercase;
                letter-spacing: 0.5px;
                position: sticky;
                top: 0;
                z-index: 5;
            }

            td {
                padding: 12px 15px;
                border-top: 1px solid #334155;
                font-size: 0.9rem;
            }

            tr:hover td {
                background: rgba(255, 255, 255, 0.02);
            }

            .status-pill {
                padding: 4px 10px;
                border-radius: 20px;
                font-size: 0.8rem;
                font-weight: 500;
                display: inline-flex;
                align-items: center;
                gap: 5px;
            }

            .status-ready {
                background: rgba(16, 185, 129, 0.1);
                color: var(--success);
            }

            .status-processing {
                background: rgba(0, 122, 255, 0.1);
                color: var(--accent);
            }

            .status-pending {
                background: rgba(245, 158, 11, 0.1);
                color: var(--warning);
            }

            .chapter-check {
                width: 18px;
                height: 18px;
                accent-color: var(--accent);
                cursor: pointer;
            }

            .progress-bar {
                width: 100px;
                height: 4px;
                background: #334155;
                border-radius: 2px;
                overflow: hidden;
            }

            .progress-fill {
                height: 100%;
                background: var(--success);
                border-radius: 2px;
                transition: width 0.3s ease;
            }

            .loader {
                border: 2px solid #334155;
                border-top: 2px solid var(--accent);
                border-radius: 50%;
                width: 14px;
                height: 14px;
                animation: spin 0.8s linear infinite;
                display: inline-block;
            }

            @keyframes spin {
                0% { transform: rotate(0deg); }
                100% { transform: rotate(360deg); }
            }

            .hidden {
                display: none !important;
            }

            /* Modal */
            .modal {
                display: none;
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background: rgba(0, 0, 0, 0.5);
                z-index: 1000;
                justify-content: center;
                align-items: center;
            }

            .modal.active {
                display: flex;
            }

            .modal-content {
                background: var(--card);
                border: 1px solid #334155;
                border-radius: 12px;
                width: 500px;
                max-width: 90%;
                max-height: 80vh;
                overflow-y: auto;
            }

            .modal-header {
                padding: 20px;
                border-bottom: 1px solid #334155;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }

            .modal-header h2 {
                font-size: 1.2rem;
                font-weight: 600;
            }

            .modal-close {
                background: none;
                border: none;
                color: #64748b;
                font-size: 1.2rem;
                cursor: pointer;
            }

            .modal-body {
                padding: 20px;
            }

            .modal-footer {
                padding: 20px;
                border-top: 1px solid #334155;
                display: flex;
                justify-content: flex-end;
                gap: 10px;
            }

            .form-group {
                margin-bottom: 15px;
            }

            .form-group label {
                display: block;
                margin-bottom: 5px;
                color: #94a3b8;
                font-size: 0.9rem;
            }

            .form-group input,
            .form-group select {
                width: 100%;
                padding: 10px;
                background: #1e293b;
                border: 1px solid #334155;
                border-radius: 6px;
                color: white;
                outline: none;
            }

            .form-group input:focus,
            .form-group select:focus {
                border-color: var(--accent);
            }

            /* Toast */
            .toast {
                position: fixed;
                bottom: 20px;
                right: 20px;
                background: var(--card);
                border: 1px solid #334155;
                border-radius: 8px;
                padding: 15px 20px;
                box-shadow: 0 10px 30px rgba(0, 0, 0, 0.3);
                transform: translateX(400px);
                transition: transform 0.3s ease;
                z-index: 1001;
                max-width: 350px;
            }

            .toast.show {
                transform: translateX(0);
            }

            .toast.success {
                border-left: 4px solid var(--success);
            }

            .toast.error {
                border-left: 4px solid var(--danger);
            }

            .toast.info {
                border-left: 4px solid var(--accent);
            }
        </style>
    </head>
    <body>
        <!-- Sidebar -->
        <div id="sidebar">
            <div class="sidebar-header">
                <h3>📚 My Library</h3>
                <button class="sidebar-toggle" onclick="toggleSidebar()">
                    <i class="fas fa-chevron-left"></i>
                </button>
            </div>
            <div class="book-search">
                <input type="text" id="bookSearch" placeholder="Search books..." onkeyup="filterBooks()">
            </div>
            <div id="bookList" class="book-list">
                <!-- Books will be loaded here -->
            </div>
            <div style="padding: 15px; border-top: 1px solid #334155;">
                <button class="btn btn-success" style="width: 100%;" onclick="showAddBookModal()">
                    <i class="fas fa-plus"></i> Add Book
                </button>
            </div>
        </div>

        <!-- Main Content -->
        <div id="main-content">
            <div class="main-header">
                <div class="book-title-header">
                    <h1 id="currentBookTitle">Select a Book</h1>
                    <span id="currentBookStatus" class="book-status-badge">0/0 chapters ready</span>
                </div>
                <div class="header-actions">
                    <button class="btn btn-secondary" onclick="refreshCurrentBook()">
                        <i class="fas fa-sync-alt"></i> Refresh
                    </button>
                    <button class="btn btn-warning" onclick="scanFolder()">
                        <i class="fas fa-folder-open"></i> Scan Folder
                    </button>
                </div>
            </div>

            <div id="workspace">
                <!-- Stats -->
                <div class="stats-grid">
                    <div class="stat-card">
                        <div class="stat-label">Total Chapters</div>
                        <div class="stat-value" id="totalChapters">0</div>
                        <div class="stat-sub">in current book</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Ready Chapters</div>
                        <div class="stat-value" id="readyChapters">0</div>
                        <div class="stat-sub"><span id="readyPercent">0</span>% complete</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Processing</div>
                        <div class="stat-value" id="processingCount">0</div>
                        <div class="stat-sub">in queue</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Total Books</div>
                        <div class="stat-value" id="totalBooks">0</div>
                        <div class="stat-sub">in library</div>
                    </div>
                </div>

                <!-- Controls -->
                <div class="controls-bar">
                    <div style="display: flex; gap: 10px; align-items: center;">
                        <input type="checkbox" id="selectAll" onclick="toggleAll(this)">
                        <span style="color: #94a3b8;">Select All</span>
                    </div>
                    
                    <div style="display: flex; gap: 8px; align-items: center; border-left: 1px solid #334155; padding-left: 15px;">
                        <input type="number" id="rangeFrom" placeholder="From" style="width: 70px; background: #1e293b; border: 1px solid #334155; color: white; padding: 8px; border-radius: 6px;">
                        <span style="color: #64748b;">-</span>
                        <input type="number" id="rangeTo" placeholder="To" style="width: 70px; background: #1e293b; border: 1px solid #334155; color: white; padding: 8px; border-radius: 6px;">
                        <button class="btn btn-secondary" onclick="selectRange()">Select Range</button>
                    </div>

                    <div style="display: flex; gap: 10px; margin-left: auto;">
                        <select id="batchVoice" class="filter-select">
                            <option value="af_heart">af_heart (F)</option>
                            <option value="am_adam" selected>am_adam (M)</option>
                        </select>
                        <select id="batchSpeed" class="filter-select">
                            <option value="1.0">1.0x</option>
                            <option value="1.2" selected>1.2x</option>
                            <option value="1.5">1.5x</option>
                            <option value="2.0">2.0x</option>
                        </select>
                        <button class="btn btn-success" id="batchBtn" onclick="generateSelected()">
                            <i class="fas fa-play"></i> Generate Selected
                        </button>
                    </div>
                </div>

                <!-- Search -->
                <div class="search-box" style="margin-bottom: 20px;">
                    <i class="fas fa-search"></i>
                    <input type="text" id="chapterSearch" placeholder="Search chapters..." onkeyup="filterChapters()">
                </div>

                <!-- Chapters Table -->
                <div class="table-container">
                    <table>
                        <thead>
                            <tr>
                                <th style="width: 30px;"></th>
                                <th style="width: 50px;">#</th>
                                <th>Chapter Title</th>
                                <th style="width: 100px;">Progress</th>
                                <th style="width: 120px;">Status</th>
                                <th style="width: 150px;">Actions</th>
                            </tr>
                        </thead>
                        <tbody id="chapterBody">
                            <tr>
                                <td colspan="6" style="text-align: center; padding: 40px; color: #64748b;">
                                    Select a book to view chapters
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- Add Book Modal -->
        <div class="modal" id="addBookModal">
            <div class="modal-content">
                <div class="modal-header">
                    <h2>Add New Book</h2>
                    <button class="modal-close" onclick="closeModal('addBookModal')">&times;</button>
                </div>
                <div class="modal-body">
                    <form id="addBookForm" onsubmit="registerBook(event)">
                        <div class="form-group">
                            <label for="bookTitle">Title *</label>
                            <input type="text" id="bookTitle" required placeholder="Enter book title">
                        </div>
                        <div class="form-group">
                            <label for="bookAuthor">Author</label>
                            <input type="text" id="bookAuthor" placeholder="Enter author name">
                        </div>
                        <div class="form-group">
                            <label for="bookFile">Filename *</label>
                            <input type="text" id="bookFile" required placeholder="book.epub">
                            <small style="color: #64748b; display: block; margin-top: 5px;">
                                File must be in: storage/books/
                            </small>
                        </div>
                    </form>
                </div>
                <div class="modal-footer">
                    <button class="btn btn-secondary" onclick="closeModal('addBookModal')">Cancel</button>
                    <button class="btn btn-success" onclick="document.getElementById('addBookForm').submit()">Add Book</button>
                </div>
            </div>
        </div>

        <!-- Toast -->
        <div class="toast" id="toast">
            <span id="toastMessage"></span>
        </div>

        <script>
            // State
            let books = [];
            let currentBookId = null;
            let chapters = [];
            let readyIds = [];
            let activeQueue = [];
            let pollInterval = null;

            // API Base
            const API_BASE = '/api/mobile';

            // Initialize
            document.addEventListener('DOMContentLoaded', () => {
                loadBooks();
                startPolling();
            });

            // Toast
            function showToast(message, type = 'info') {
                const toast = document.getElementById('toast');
                const toastMessage = document.getElementById('toastMessage');
                
                toast.className = 'toast ' + type;
                toastMessage.textContent = message;
                toast.classList.add('show');
                
                setTimeout(() => {
                    toast.classList.remove('show');
                }, 3000);
            }

            // Modal
            function showModal(modalId) {
                document.getElementById(modalId).classList.add('active');
            }

            function closeModal(modalId) {
                document.getElementById(modalId).classList.remove('active');
            }

            // Sidebar
            function toggleSidebar() {
                const sidebar = document.getElementById('sidebar');
                sidebar.classList.toggle('collapsed');
            }

            // Load Books
            async function loadBooks() {
                try {
                    const response = await fetch(API_BASE + '/books');
                    const data = await response.json();
                    books = data.books || [];
                    renderBookList(books);
                    
                    document.getElementById('totalBooks').textContent = books.length;
                    
                    if (books.length > 0 && !currentBookId) {
                        selectBook(books[0].id);
                    }
                } catch (error) {
                    console.error('Error loading books:', error);
                    showToast('Failed to load books', 'error');
                }
            }

            // Render Book List
            function renderBookList(books) {
                const list = document.getElementById('bookList');
                
                if (books.length === 0) {
                    list.innerHTML = '<div style="padding: 20px; text-align: center; color: #64748b;">No books found</div>';
                    return;
                }

                list.innerHTML = books.map(book => {
                    const progress = book.total_chapters ? Math.round((book.ready_chapters / book.total_chapters) * 100) : 0;
                    return `
                        <div class="book-item ${currentBookId === book.id ? 'active' : ''}" onclick="selectBook('${book.id}')">
                            <div class="book-icon">${book.title.charAt(0)}</div>
                            <div class="book-info">
                                <div class="book-title">${escapeHtml(book.title)}</div>
                                <div class="book-meta">
                                    <span class="book-badge">${book.total_chapters || 0} ch</span>
                                    <span class="book-badge ready">${book.ready_chapters || 0} ready</span>
                                    <span>${progress}%</span>
                                </div>
                            </div>
                        </div>
                    `;
                }).join('');
            }

            // Filter Books
            function filterBooks() {
                const query = document.getElementById('bookSearch').value.toLowerCase();
                const filtered = books.filter(book => 
                    book.title.toLowerCase().includes(query) ||
                    (book.author && book.author.toLowerCase().includes(query))
                );
                renderBookList(filtered);
            }

            // Select Book
            async function selectBook(bookId) {
                currentBookId = bookId;
                const book = books.find(b => b.id === bookId);
                
                if (book) {
                    document.getElementById('currentBookTitle').textContent = book.title;
                    document.getElementById('currentBookStatus').textContent = 
                        `${book.ready_chapters || 0}/${book.total_chapters || 0} chapters ready`;
                }

                // Update active state in sidebar
                document.querySelectorAll('.book-item').forEach(item => {
                    item.classList.remove('active');
                });
                const activeItem = document.querySelector(`.book-item[onclick*="'${bookId}'"]`);
                if (activeItem) activeItem.classList.add('active');

                await loadChapters(bookId);
            }

            // Load Chapters
            async function loadChapters(bookId) {
                try {
                    const response = await fetch(API_BASE + `/books/${bookId}/chapters?limit=1000`);
                    const data = await response.json();
                    
                    chapters = data.items || [];
                    
                    // Update stats
                    const readyCount = chapters.filter(c => c.is_ready).length;
                    document.getElementById('totalChapters').textContent = chapters.length;
                    document.getElementById('readyChapters').textContent = readyCount;
                    
                    const percent = chapters.length ? Math.round((readyCount / chapters.length) * 100) : 0;
                    document.getElementById('readyPercent').textContent = percent;

                    renderChapters(chapters);
                } catch (error) {
                    console.error('Error loading chapters:', error);
                    showToast('Failed to load chapters', 'error');
                }
            }

            // Render Chapters
            function renderChapters(chapters) {
                const tbody = document.getElementById('chapterBody');
                
                if (chapters.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; padding: 40px; color: #64748b;">No chapters found</td></tr>';
                    return;
                }

                tbody.innerHTML = chapters.map((chapter, index) => {
                    const isReady = chapter.is_ready || readyIds.includes(chapter.id);
                    const isProcessing = activeQueue.includes(chapter.id);
                    
                    let statusClass = 'status-pending';
                    let statusText = 'Pending';
                    
                    if (isReady) {
                        statusClass = 'status-ready';
                        statusText = '✅ Ready';
                    } else if (isProcessing) {
                        statusClass = 'status-processing';
                        statusText = '<div class="loader" style="margin-right: 5px;"></div> Processing';
                    }

                    return `
                        <tr id="row-${chapter.id}">
                            <td>
                                <input type="checkbox" class="chapter-check" data-id="${chapter.id}" 
                                       data-name="${escapeHtml(chapter.name)}" data-idx="${index + 1}"
                                       ${isReady ? 'disabled' : ''}>
                            </td>
                            <td style="color: #64748b;">${index + 1}</td>
                            <td>
                                <div style="font-weight: 500;">${escapeHtml(chapter.name)}</div>
                                <div style="font-size: 0.7rem; color: #64748b;">ID: ${chapter.id}</div>
                            </td>
                            <td>
                                <div class="progress-bar">
                                    <div class="progress-fill" style="width: ${isReady ? '100' : '0'}%"></div>
                                </div>
                            </td>
                            <td>
                                <span class="status-pill ${statusClass}">
                                    ${statusText}
                                </span>
                            </td>
                            <td>
                                <div style="display: flex; gap: 5px;">
                                    ${isReady ? `
                                        <button class="btn btn-primary" style="padding: 5px 10px;" 
                                                onclick="downloadChapter('${chapter.id}')">
                                            <i class="fas fa-download"></i>
                                        </button>
                                        <button class="btn btn-secondary" style="padding: 5px 10px;" 
                                                onclick="viewMetadata('${chapter.id}')">
                                            <i class="fas fa-chart-bar"></i>
                                        </button>
                                    ` : `
                                        <button class="btn btn-secondary" style="padding: 5px 10px;" 
                                                onclick="generateSingle('${chapter.id}', '${escapeHtml(chapter.name)}')"
                                                ${isProcessing ? 'disabled' : ''}>
                                            <i class="fas fa-play"></i>
                                        </button>
                                    `}
                                </div>
                            </td>
                        </tr>
                    `;
                }).join('');
            }

            // Filter Chapters
            function filterChapters() {
                const query = document.getElementById('chapterSearch').value.toLowerCase();
                const rows = document.querySelectorAll('#chapterBody tr');
                
                rows.forEach(row => {
                    if (row.cells && row.cells[2]) {
                        const title = row.cells[2].textContent.toLowerCase();
                        row.style.display = title.includes(query) ? '' : 'none';
                    }
                });
            }

            // Select Range
            function selectRange() {
                const from = parseInt(document.getElementById('rangeFrom').value);
                const to = parseInt(document.getElementById('rangeTo').value);
                
                if (isNaN(from) || isNaN(to)) {
                    showToast('Enter valid numbers', 'error');
                    return;
                }

                document.querySelectorAll('.chapter-check:not(:disabled)').forEach(cb => {
                    const idx = parseInt(cb.dataset.idx);
                    cb.checked = (idx >= from && idx <= to);
                });
            }

            // Toggle All
            function toggleAll(source) {
                document.querySelectorAll('.chapter-check:not(:disabled)').forEach(cb => {
                    cb.checked = source.checked;
                });
            }

            // Generate Single
            async function generateSingle(id, name) {
                const btn = document.querySelector(`[onclick*="'${id}'"]`);
                if (btn) btn.disabled = true;

                try {
                    const txtRes = await fetch(API_BASE + `/book-content/${currentBookId}/${id}`);
                    const txtData = await txtRes.json();

                    await fetch('/tts/chapter', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            text: txtData.content,
                            chapter_name: name,
                            epub_item_id: id,
                            voice: document.getElementById('batchVoice').value,
                            speed: parseFloat(document.getElementById('batchSpeed').value)
                        })
                    });

                    showToast('Chapter queued for generation', 'success');
                } catch (error) {
                    console.error('Error generating chapter:', error);
                    showToast('Failed to queue chapter', 'error');
                } finally {
                    if (btn) btn.disabled = false;
                }
            }

            // Generate Selected
            async function generateSelected() {
                const selected = Array.from(document.querySelectorAll('.chapter-check:checked'));
                
                if (selected.length === 0) {
                    showToast('Select chapters first', 'error');
                    return;
                }

                if (!confirm(`Generate ${selected.length} chapters?`)) return;

                const batchBtn = document.getElementById('batchBtn');
                batchBtn.disabled = true;
                batchBtn.innerHTML = '<div class="loader"></div> Generating...';

                try {
                    const batchRequests = [];
                    
                    for (const check of selected) {
                        const id = check.dataset.id;
                        const name = check.dataset.name;
                        
                        const txtRes = await fetch(API_BASE + `/book-content/${currentBookId}/${id}`);
                        const txtData = await txtRes.json();
                        
                        batchRequests.push({
                            text: txtData.content,
                            voice: document.getElementById('batchVoice').value,
                            speed: parseFloat(document.getElementById('batchSpeed').value),
                            book_title: document.getElementById('currentBookTitle').textContent,
                            chapter_name: name,
                            epub_item_id: id
                        });
                        
                        check.checked = false;
                    }

                    const response = await fetch('/tts/batch', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(batchRequests)
                    });

                    if (response.ok) {
                        showToast(`Queued ${selected.length} chapters`, 'success');
                    } else {
                        throw new Error('Failed to queue');
                    }
                } catch (error) {
                    console.error('Error in batch generation:', error);
                    showToast('Failed to queue chapters', 'error');
                } finally {
                    batchBtn.disabled = false;
                    batchBtn.innerHTML = '<i class="fas fa-play"></i> Generate Selected';
                }
            }

            // Download Chapter
            function downloadChapter(chapterId) {
                window.open(API_BASE + `/download/${currentBookId}/${chapterId}`, '_blank');
            }

            // View Metadata
            async function viewMetadata(chapterId) {
                try {
                    const response = await fetch(API_BASE + `/metadata/${currentBookId}/${chapterId}`);
                    const metadata = await response.json();
                    
                    console.log('Metadata:', metadata);
                    alert(JSON.stringify(metadata, null, 2).slice(0, 500) + '...');
                } catch (error) {
                    console.error('Error loading metadata:', error);
                    showToast('Failed to load metadata', 'error');
                }
            }

            // Add Book
            function showAddBookModal() {
                document.getElementById('bookTitle').value = '';
                document.getElementById('bookAuthor').value = '';
                document.getElementById('bookFile').value = '';
                showModal('addBookModal');
            }

            async function registerBook(event) {
                event.preventDefault();
                
                const title = document.getElementById('bookTitle').value;
                const author = document.getElementById('bookAuthor').value;
                const filename = document.getElementById('bookFile').value;

                try {
                    let url = API_BASE + `/books/register?title=${encodeURIComponent(title)}&filename=${encodeURIComponent(filename)}`;
                    if (author) url += `&author=${encodeURIComponent(author)}`;

                    const response = await fetch(url, { method: 'POST' });
                    
                    if (!response.ok) throw new Error('Registration failed');
                    
                    closeModal('addBookModal');
                    showToast('Book registered successfully', 'success');
                    loadBooks();
                } catch (error) {
                    console.error('Error registering book:', error);
                    showToast('Failed to register book', 'error');
                }
            }

            // Refresh Current Book
            async function refreshCurrentBook() {
                if (!currentBookId) return;

                try {
                    showToast('Refreshing book...', 'info');
                    
                    const response = await fetch(API_BASE + `/books/${currentBookId}/refresh`, { method: 'POST' });
                    
                    if (!response.ok) throw new Error('Refresh failed');
                    
                    showToast('Book refreshed', 'success');
                    loadBooks();
                    loadChapters(currentBookId);
                } catch (error) {
                    console.error('Error refreshing book:', error);
                    showToast('Failed to refresh book', 'error');
                }
            }

            // Scan Folder
            async function scanFolder() {
                try {
                    showToast('Scanning books folder...', 'info');
                    
                    const response = await fetch(API_BASE + '/scan-books-folder', { method: 'POST' });
                    const result = await response.json();
                    
                    showToast(`Scan complete: ${result.registered} new books`, 'success');
                    loadBooks();
                } catch (error) {
                    console.error('Error scanning folder:', error);
                    showToast('Failed to scan folder', 'error');
                }
            }

            // Polling for status updates
            function startPolling() {
                if (pollInterval) clearInterval(pollInterval);
                
                pollInterval = setInterval(async () => {
                    if (!currentBookId) return;

                    try {
                        const [statusRes, queueRes] = await Promise.all([
                            fetch('/book/status'),
                            fetch('/book/queue')
                        ]);

                        readyIds = await statusRes.json();
                        activeQueue = await queueRes.json();

                        // Update chapter rows
                        chapters.forEach(chapter => {
                            const row = document.getElementById(`row-${chapter.id}`);
                            if (row) {
                                const statusCell = row.querySelector('.status-pill');
                                const isReady = readyIds.includes(chapter.id);
                                const isProcessing = activeQueue.includes(chapter.id);
                                
                                if (isReady) {
                                    statusCell.className = 'status-pill status-ready';
                                    statusCell.innerHTML = '✅ Ready';
                                } else if (isProcessing) {
                                    statusCell.className = 'status-pill status-processing';
                                    statusCell.innerHTML = '<div class="loader" style="margin-right: 5px;"></div> Processing';
                                } else {
                                    statusCell.className = 'status-pill status-pending';
                                    statusCell.innerHTML = 'Pending';
                                }
                            }
                        });

                        // Update processing count
                        document.getElementById('processingCount').textContent = activeQueue.length;

                        // Update book list if needed
                        if (books.length > 0) {
                            const readyChapters = chapters.filter(c => readyIds.includes(c.id)).length;
                            const percent = chapters.length ? Math.round((readyChapters / chapters.length) * 100) : 0;
                            document.getElementById('currentBookStatus').textContent = 
                                `${readyChapters}/${chapters.length} chapters ready (${percent}%)`;
                        }

                    } catch (error) {
                        console.error('Polling error:', error);
                    }
                }, 3000);
            }

            // Escape HTML
            function escapeHtml(unsafe) {
                if (!unsafe) return '';
                return unsafe
                    .replace(/&/g, "&amp;")
                    .replace(/</g, "&lt;")
                    .replace(/>/g, "&gt;")
                    .replace(/"/g, "&quot;")
                    .replace(/'/g, "&#039;");
            }

            // Cleanup on page unload
            window.addEventListener('beforeunload', () => {
                if (pollInterval) clearInterval(pollInterval);
            });
        </script>
    </body>
    </html>
    """)