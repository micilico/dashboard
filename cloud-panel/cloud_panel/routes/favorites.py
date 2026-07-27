from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Form

from common import error_detail

from ..models import get_favorites, add_favorite, remove_favorite
from .csrf_guard import require_action_guard

router = APIRouter()


@router.get("/favorites")
async def list_favorites(request: Request):
    try:
        return {"items": get_favorites()}
    except Exception:
        raise HTTPException(
            status_code=500,
            detail=error_detail("db_error", "Favoris indisponibles.", "Réessayer"),
        )


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
    except Exception:
        raise HTTPException(
            status_code=500,
            detail=error_detail("db_error", "Ajout du favori impossible.", "Réessayer"),
        )


@router.post("/favorites/remove")
async def remove_fav(
    request: Request,
    _=Depends(require_action_guard),
    path: str = Form(...),
):
    try:
        result = remove_favorite(path)
        return result
    except Exception:
        raise HTTPException(
            status_code=500,
            detail=error_detail("db_error", "Suppression du favori impossible.", "Réessayer"),
        )
