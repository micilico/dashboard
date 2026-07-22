from __future__ import annotations

import os
import tempfile
import zipfile
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, UploadFile, File, Form, Response
from fastapi.responses import FileResponse

from ..config import MOUNT_PATH
from ..security import resolve_path_within
from ..storage import (
    list_directory,
    upload_file_streaming,
    download_file,
    create_directory,
    rename_item,
    delete_item,
    clear_scandir_cache,
)
from .csrf_guard import require_action_guard, set_csrf_cookie

router = APIRouter()


@router.get("/session")
async def session(request: Request, response: Response) -> dict[str, str]:
    response.headers["Cache-Control"] = "no-store"
    return {"csrfToken": set_csrf_cookie(request, response)}


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
    _=Depends(require_action_guard),
    path: str = Form(""),
    file: UploadFile = File(...),
):
    """Upload file with streaming."""
    try:
        result = await upload_file_streaming(path, file)
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
async def mkdir(
    request: Request,
    _=Depends(require_action_guard),
    path: str = Form(""),
    name: str = Form(...),
):
    """Create directory."""
    try:
        result = create_directory(path, name)
        return result
    except ValueError as e:
        raise HTTPException(status_code=403, detail={"code": "path_error", "message": str(e), "recovery": "Verifier le chemin"})


@router.post("/files/rename")
async def rename(
    request: Request,
    _=Depends(require_action_guard),
    path: str = Form(""),
    old_name: str = Form(...),
    new_name: str = Form(...),
):
    """Rename file or directory."""
    try:
        result = rename_item(path, old_name, new_name)
        return result
    except ValueError as e:
        raise HTTPException(status_code=403, detail={"code": "path_error", "message": str(e), "recovery": "Verifier le chemin"})


@router.post("/files/delete")
async def delete(
    request: Request,
    _=Depends(require_action_guard),
    path: str = Form(""),
    name: str = Form(...),
):
    """Delete file or directory."""
    try:
        result = delete_item(path, name)
        return result
    except ValueError as e:
        raise HTTPException(status_code=403, detail={"code": "path_error", "message": str(e), "recovery": "Verifier le chemin"})


@router.post("/files/download-zip")
async def download_zip(
    request: Request,
    tasks: BackgroundTasks,
    _=Depends(require_action_guard),
    paths: str = Form(...),
):
    """Download multiple files as a ZIP archive."""
    try:
        file_list = [p.strip() for p in paths.split("\n") if p.strip()]
        if not file_list:
            raise HTTPException(status_code=400, detail={"code": "no_files", "message": "Aucun fichier selectionne.", "recovery": "Selectionner des fichiers"})

        tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for rel_path in file_list:
                abs_path = resolve_path_within(MOUNT_PATH, rel_path, must_exist=True)
                if os.path.isfile(abs_path):
                    zf.write(abs_path, os.path.basename(abs_path))
        tmp.close()
        tmp_path = tmp.name
        tasks.add_task(os.unlink, tmp_path)

        return FileResponse(tmp_path, filename="cloud-panel-bulk.zip", media_type="application/zip",
                            headers={"Content-Disposition": "attachment; filename=cloud-panel-bulk.zip"})
    except ValueError as e:
        raise HTTPException(status_code=403, detail={"code": "path_error", "message": str(e), "recovery": "Verifier le chemin"})
    except Exception as e:
        raise HTTPException(status_code=500, detail={"code": "zip_error", "message": str(e), "recovery": "Reessayer"})


@router.post("/files/refresh")
async def refresh(
    request: Request,
    _=Depends(require_action_guard),
):
    """Clear cache and refresh directory listing."""
    clear_scandir_cache()
    return {"success": True, "message": "Cache vide"}
