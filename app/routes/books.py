from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import timedelta

from app.schemas import BookCreate, Book as BookSchema
from app.controllers import book_controller
from app.database import SessionLocal
from app.auth import get_current_user

router = APIRouter(prefix="/books", tags=["books"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("", response_model=BookSchema)
def create_book(book: BookCreate, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    return book_controller.create_book(db, book)


@router.get("", response_model=list[BookSchema])
def read_books(db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    return book_controller.get_books(db)


@router.get("/{book_id}", response_model=BookSchema)
def read_book(book_id: int, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    book = book_controller.get_book(db, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    return book


@router.put("/{book_id}", response_model=BookSchema)
def update_book(book_id: int, book: BookCreate, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    updated_book = book_controller.update_book(db, book_id, book)
    if not updated_book:
        raise HTTPException(status_code=404, detail="Book not found")
    return updated_book


@router.delete("/{book_id}")
def delete_book(book_id: int, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    book = book_controller.delete_book(db, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    return {"message": "Book deleted"}
