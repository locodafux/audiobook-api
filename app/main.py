import os
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.database import engine
from app.models import Book, User
from app.services.kokoro_service import init_kokoro
from app.routes import books, users, tts, epub_tts, mobile_router

# Create database tables
Book.metadata.create_all(bind=engine)
User.metadata.create_all(bind=engine)

app = FastAPI(title="Audiobook API - Mobile Ready")

# CORS CONFIGURATION - Essential for Mobile
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup_event():
    print("🚀 Initializing Kokoro TTS...")
    init_kokoro()
    print("✅ System Ready")

# Include all routers
app.include_router(books.router)
app.include_router(users.router)
app.include_router(tts.router, prefix="", tags=["TTS"])
app.include_router(epub_tts.router, prefix="", tags=["EPUB_TTS"])
app.include_router(mobile_router.router, tags=["Mobile"])

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 80))
    # MUST use 0.0.0.0 for phone connectivity
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)