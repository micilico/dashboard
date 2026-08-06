from __future__ import annotations

import os
import tempfile
import zipfile
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, UploadFile, File, Form, Response, Query
from fastapi.responses import FileResponse

from common import error_detail

from ..config import MOUNT_PATH
from ..security import resolve_path_within
from ..storage import (
    list_directory,
    upload_file_streaming,
    download_file,
    create_directory,
    rename_item,
    move_item,
    delete_item,
    clear_scandir_cache,
    get_folder_size,
)
from ..services.media import apply_organization_plan, build_organization_plan
from .csrf_guard import require_action_guard, set_csrf_cookie

router = APIRouter()


@router.get("/session")
async def session(request: Request, response: Response) -> dict[str, str]:
    response.headers["Cache-Control"] = "no-store"
    return {"csrfToken": set_csrf_cookie(request, response)}


@router.get("/files")
async def get_files(
    request: Request,
    path: str = "",
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    search: str = Query("", max_length=200),
):
    """List directory contents."""
    try:
        result = list_directory(path, offset=offset, limit=limit, search=search)
        return result
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=error_detail("path_error", "Chemin non autorisé ou introuvable.", "Vérifier le chemin"),
        )


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
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=error_detail("path_error", "Destination non autorisée.", "Vérifier le chemin"),
        )
    except Exception:
        raise HTTPException(
            status_code=500,
            detail=error_detail("upload_error", "Téléversement impossible.", "Réessayer"),
        )


@router.get("/files/download")
async def download_file_endpoint(path: str):
    """Download file."""
    try:
        file_path = download_file(path)
        return FileResponse(file_path, filename=os.path.basename(file_path))
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=error_detail("path_error", "Fichier non autorisé ou introuvable.", "Vérifier le chemin"),
        )


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
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=error_detail("path_error", "Création impossible à cet emplacement.", "Vérifier le chemin"),
        )


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
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=error_detail("path_error", "Renommage impossible à cet emplacement.", "Vérifier le chemin"),
        )


@router.post("/files/move")
async def move(
    request: Request,
    _=Depends(require_action_guard),
    path: str = Form(""),
    name: str = Form(...),
    dest: str = Form(""),
):
    """Move a file or directory to another folder."""
    try:
        result = move_item(path, name, dest)
        return result
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=error_detail("path_error", "Déplacement impossible à cet emplacement.", "Vérifier le chemin et la destination"),
        )
    except Exception:
        raise HTTPException(
            status_code=500,
            detail=error_detail("move_error", "Déplacement impossible.", "Réessayer"),
        )


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
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=error_detail("path_error", "Suppression impossible à cet emplacement.", "Vérifier le chemin"),
        )


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
            raise HTTPException(
                status_code=400,
                detail=error_detail("no_files", "Aucun fichier sélectionné.", "Sélectionner des fichiers"),
            )

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
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=error_detail("path_error", "Un fichier sélectionné n’est pas autorisé.", "Vérifier la sélection"),
        )
    except Exception:
        raise HTTPException(
            status_code=500,
            detail=error_detail("zip_error", "Création de l’archive impossible.", "Réessayer"),
        )


@router.post("/files/refresh")
async def refresh(
    request: Request,
    _=Depends(require_action_guard),
):
    """Clear cache and refresh directory listing."""
    clear_scandir_cache()
    return {"success": True, "message": "Cache vide"}


@router.post("/files/size")
async def folder_size(
    request: Request,
    _=Depends(require_action_guard),
    path: str = Form(""),
    name: str = Form(...),
):
    """Compute the recursive size of a folder."""
    try:
        return get_folder_size(path, name)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=error_detail("path_error", "Dossier non autorisé ou introuvable.", "Vérifier le chemin"),
        )
    except Exception:
        raise HTTPException(
            status_code=500,
            detail=error_detail("size_error", "Calcul de la taille impossible.", "Réessayer"),
        )


@router.post("/files/organize/preview")
async def organize_preview(
    request: Request,
    _=Depends(require_action_guard),
    path: str = Form(""),
):
    """Preview how the directory would be reorganized (series, movies, parasites)."""
    try:
        return build_organization_plan(path)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=error_detail("path_error", "Dossier non autorisé ou introuvable.", "Vérifier le chemin"),
        )
    except Exception:
        raise HTTPException(
            status_code=500,
            detail=error_detail("organize_error", "Analyse impossible.", "Réessayer"),
        )


@router.post("/files/organize/apply")
async def organize_apply(
    request: Request,
    _=Depends(require_action_guard),
    path: str = Form(""),
):
    """Group seasons into series folders, rename them, and move movies to Films/."""
    try:
        result = apply_organization_plan(path)
        return result
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=error_detail("path_error", "Dossier non autorisé ou introuvable.", "Vérifier le chemin"),
        )
    except Exception:
        raise HTTPException(
            status_code=500,
            detail=error_detail("organize_error", "Réorganisation impossible.", "Réessayer"),
        )
