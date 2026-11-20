"""
Publishing history routes.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.schemas import HistoryListResponse
from app.repositories.history_repository import HistoryRepository

router = APIRouter()


@router.get("", response_model=HistoryListResponse)
async def get_history(
    page: int = 1,
    page_size: int = 20,
    status: str = Query(None),
    db: Session = Depends(get_db),
):
    """Get publishing history."""
    repo = HistoryRepository(db)
    items, total = repo.get_all(page=page, page_size=page_size, status=status)
    return HistoryListResponse(items=items, total=total)
