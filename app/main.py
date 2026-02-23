import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware  # <-- ADDED
from app.database import engine
from app.models import Book, User
from app.services.kokoro_service import init_kokoro

# ----------------------------
# Create database tables
# ----------------------------
Book.metadata.create_all(bind=engine)
User.metadata.create_all(bind=engine)

# ----------------------------
# Initialize FastAPI app
# ----------------------------
app = FastAPI(title="Audiobook API with TTS")

# ----------------------------
# CORS Configuration (ADDED FOR MOBILE CONNECTIVITY)
# ----------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows your phone to talk to your Mac
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# ----------------------------
# Initialize Kokoro on startup
# ----------------------------
@app.on_event("startup")
def startup_event():
    print("Initializing Kokoro TTS pipeline...")
    init_kokoro()
    print("Kokoro initialized.")

# ----------------------------
# Include routers
# ----------------------------
from app.routes import books, users, tts, epub_tts

app.include_router(books.router)
app.include_router(users.router)
app.include_router(tts.router, prefix="", tags=["TTS"])
app.include_router(epub_tts.router, prefix="", tags=["EPUB_TTS"])

# ----------------------------
# Run uvicorn (local dev)
# ----------------------------
if __name__ == "__main__":
    import uvicorn
    # Use Railway's port if provided, else default to 8000
    port = int(os.environ.get("PORT", 8000))
    # Note: host="0.0.0.0" is correct for local network access
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)