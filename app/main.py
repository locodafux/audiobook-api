import os
from fastapi import FastAPI
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
from app.routes import books, users, tts

app.include_router(books.router, prefix="/books", tags=["Books"])
app.include_router(users.router, prefix="/users", tags=["Users"])
app.include_router(tts.router, prefix="", tags=["TTS"])

# ----------------------------
# Run uvicorn (local dev)
# ----------------------------
if __name__ == "__main__":
    import uvicorn
    # Use Railway's port if provided, else default to 8000
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)
