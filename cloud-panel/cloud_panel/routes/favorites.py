from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Form

from ..models import get_favorites, add_favorite, remove_favorite
from .csrf_guard import require_action_guard

router = APIRouter()


@router.get("/favorites")
async def list_favorites(request: Request):
    try:
        return {"items": get_favorites()}
    except Exception as e:
        raise HTTPException(status_code=500, detail={"code": "db_error", "message": str(e), "recovery": "Reessayer"})


@router.post("/favorites/add")
async def add_fav(
    request: Request,
    _=Depends(require_action_guard),
    path: str = Form(...),
    name: str = Form(...),
    is_dir: bool = Form(False),
):
    try:
        result = add_favorite(path, name, is_dir)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail={"code": "db_error", "message": str(e), "recovery": "Reessayer"})


@router.post("/favorites/remove")
async def remove_fav(
    request: Request,
    _=Depends(require_action_guard),
    path: str = Form(...),
):
    try:
        result = remove_favorite(path)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail={"code": "db_error", "message": str(e), "recovery": "Reessayer"})
