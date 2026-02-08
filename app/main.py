from fastapi import FastAPI
from app.database import engine
from app.models import Book, User

# Create tables
Book.metadata.create_all(bind=engine)
User.metadata.create_all(bind=engine)

app = FastAPI()

# Import routes
from app.routes import books, users

# Include routers
app.include_router(books.router)
app.include_router(users.router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)