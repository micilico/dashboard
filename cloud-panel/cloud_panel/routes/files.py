from __future__ import annotations

import os
from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form, Response
from fastapi.responses import FileResponse

from ..config import (
    MOUNT_PATH,
    CSRF_COOKIE,
    CSRF_TOKEN_TTL_SECONDS,
    MAX_CSRF_TOKENS,
)
from ..storage import (
    list_directory,
    upload_file_streaming,
    download_file,
    create_directory,
    rename_item,
    delete_item,
    clear_scandir_cache,
)

router = APIRouter()


@router.get("/session")
async def session(request: Request, response: Response) -> dict[str, str]:
    from common.csrf import set_csrf_cookie as _set_csrf
    response.headers["Cache-Control"] = "no-store"
    token = _set_csrf(
        request.app, request, response,
        CSRF_COOKIE, CSRF_TOKEN_TTL_SECONDS, MAX_CSRF_TOKENS,
        cookie_path="/",
    )
    return {"csrfToken": token}


@router.get("/files")
async def get_files(request: Request, path: str = ""):
    """List directory contents."""
    try:
        result = list_directory(path)
        return result
    except ValueError as e:
        raise HTTPException(status_code=403, detail={"code": "path_error", "message": str(e), "recovery": "Verifier le chemin"})


@router.post("/files/upload")
async def upload_file(
    request: Request,
    path: str = Form(""),
    file: UploadFile = File(...),
):
    """Upload file with streaming."""
    try:
        result = await upload_file_streaming(path, file.filename, file.file)
        return result
    except ValueError as e:
        raise HTTPException(status_code=403, detail={"code": "path_error", "message": str(e), "recovery": "Verifier le chemin"})
    except Exception as e:
        raise HTTPException(status_code=500, detail={"code": "upload_error", "message": str(e), "recovery": "Reessayer"})


@router.get("/files/download")
async def download_file_endpoint(path: str):
    """Download file."""
    try:
        file_path = download_file(path)
        return FileResponse(file_path, filename=os.path.basename(file_path))
    except ValueError as e:
        raise HTTPException(status_code=403, detail={"code": "path_error", "message": str(e), "recovery": "Verifier le chemin"})


@router.post("/files/mkdir")
async def mkdir(request: Request, path: str = Form(""), name: str = Form(...)):
    """Create directory."""
    try:
        result = create_directory(path, name)
        return result
    except ValueError as e:
        raise HTTPException(status_code=403, detail={"code": "path_error", "message": str(e), "recovery": "Verifier le chemin"})


@router.post("/files/rename")
async def rename(request: Request, path: str = Form(""), old_name: str = Form(...), new_name: str = Form(...)):
    """Rename file or directory."""
    try:
        result = rename_item(path, old_name, new_name)
        return result
    except ValueError as e:
        raise HTTPException(status_code=403, detail={"code": "path_error", "message": str(e), "recovery": "Verifier le chemin"})


@router.post("/files/delete")
async def delete(request: Request, path: str = Form(""), name: str = Form(...)):
    """Delete file or directory."""
    try:
        result = delete_item(path, name)
        return result
    except ValueError as e:
        raise HTTPException(status_code=403, detail={"code": "path_error", "message": str(e), "recovery": "Verifier le chemin"})


@router.post("/files/refresh")
async def refresh(request: Request):
    """Clear cache and refresh directory listing."""
    clear_scandir_cache()
    return {"success": True, "message": "Cache vide"}
