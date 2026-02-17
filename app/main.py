from fastapi import FastAPI
from app.database import engine
from app.models import Book, User

# Create tables
Book.metadata.create_all(bind=engine)
User.metadata.create_all(bind=engine)

app = FastAPI()

# Import routers
from app.routes import books, users, tts
from app.services.kokoro_service import init_kokoro

# Initialize Kokoro on startup
@app.on_event("startup")
def startup_event():
    init_kokoro()

# Include routers
app.include_router(books.router)
app.include_router(users.router)
app.include_router(tts.router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
