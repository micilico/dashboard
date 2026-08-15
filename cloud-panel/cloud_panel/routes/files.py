from __future__ import annotations

import json
import logging
import os
import secrets
from fastapi import APIRouter, Depends, Header, HTTPException, Request, UploadFile, File, Form, Response, Query
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from common import error_detail

from ..config import INTERNAL_TOKEN, MOUNT_PATH, SEARCH_MAX_RESULTS
from ..security import resolve_path_within
from ..storage import (
    list_directory,
    upload_file_streaming,
    download_file,
    create_directory,
    rename_item,
    move_item,
    move_items,
    delete_item,
    clear_scandir_cache,
    get_folder_size,
    get_folder_sizes,
    copy_item,
    list_trash,
    restore_item,
    empty_trash,
    create_text_file,
    read_text_file,
    write_text_file,
    search_files,
    get_file_properties,
)
from ..services.media import apply_organization_plan, build_organization_plan
from .csrf_guard import require_action_guard, set_csrf_cookie

router = APIRouter()
logger = logging.getLogger("cloud_panel.routes.files")


def _read_limiter(request: Request):
    limiter = getattr(request.app.state, "read_limiter", None)
    if limiter is not None and not limiter.allow(request.client.host if request.client else "unknown"):
        raise HTTPException(
            status_code=429,
            detail=error_detail("rate_limited", "Trop de requêtes en peu de temps.", "Réessayer"),
        )


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
    _read_limiter(request)
    try:
        return await run_in_threadpool(list_directory, path, offset, limit, search)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=error_detail("path_error", "Chemin non autorisé ou introuvable.", "Vérifier le chemin"),
        )


@router.get("/files/search")
async def search(
    request: Request,
    q: str = Query(..., max_length=200),
    path: str = "",
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """Recursively search file names."""
    _read_limiter(request)
    try:
        return await run_in_threadpool(search_files, q, path, offset, limit, SEARCH_MAX_RESULTS)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=error_detail("path_error", "Recherche non autorisée.", "Vérifier le chemin"),
        )
    except Exception:
        raise HTTPException(
            status_code=500,
            detail=error_detail("search_error", "Recherche impossible.", "Réessayer"),
        )


@router.post("/files/upload")
async def upload_file(
    request: Request,
    _=Depends(require_action_guard),
    path: str = Form(""),
    overwrite: str = Form("rename"),
    file: UploadFile = File(...),
):
    """Upload file with streaming."""
    try:
        result = await upload_file_streaming(path, file, overwrite=overwrite)
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
        file_path = await run_in_threadpool(download_file, path)
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
        return await run_in_threadpool(create_directory, path, name)
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
        return await run_in_threadpool(rename_item, path, old_name, new_name)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=error_detail("path_error", "Renommage impossible à cet emplacement.", "Vérifier le chemin"),
        )
    except OSError:
        raise HTTPException(
            status_code=409,
            detail=error_detail("rename_conflict", "Un élément portant ce nom existe déjà ici.", "Choisir un autre nom"),
        )


@router.post("/files/internal-arrange-batch")
async def internal_arrange_batch(
    request: Request,
    items: str = Form(...),
    x_internal_token: str = Header(default=""),
):
    """Server-to-server batch arrange used by the torrent-panel relink.

    Each item is an operation: ``{"op": "rename", path, old_name, new_name}``,
    ``{"op": "mkdir", path, name}`` or ``{"op": "move", path, old_name, new_name, dest}``.
    Guarded by a shared token (``CLOUD_PANEL_INTERNAL_TOKEN``), not by CSRF,
    because this endpoint is called by another panel, not by a browser.
    """
    if not INTERNAL_TOKEN or not secrets.compare_digest(x_internal_token, INTERNAL_TOKEN):
        raise HTTPException(
            status_code=403,
            detail=error_detail("internal_token_invalid", "Jeton interne invalide.", "Réessayer"),
        )
    try:
        parsed = json.loads(items)
        if not isinstance(parsed, list):
            raise ValueError("Sélection invalide")
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(
            status_code=403,
            detail=error_detail("path_error", "Opération impossible à cet emplacement.", "Vérifier le chemin"),
        )

    results: list[dict[str, object]] = []
    for item in parsed if isinstance(parsed, list) else []:
        op = str(item.get("op") or "rename")
        path = str(item.get("path") or "")
        old_name = str(item.get("old_name") or "")
        new_name = str(item.get("new_name") or "")
        dest = str(item.get("dest") or "")
        folder_name = str(item.get("name") or "")
        try:
            if op == "mkdir":
                target = resolve_path_within(MOUNT_PATH, os.path.join(path, folder_name), must_exist=False)
                if os.path.isdir(target):
                    result = {"success": True}
                else:
                    result = await run_in_threadpool(create_directory, path, folder_name)
                results.append({"success": bool(result.get("success", True)), "op": op, "path": path, "new_name": folder_name})
            elif op == "move":
                await run_in_threadpool(move_item, path, old_name, dest)
                if new_name and new_name != old_name:
                    await run_in_threadpool(rename_item, dest, old_name, new_name)
                results.append({"success": True, "op": op, "path": path, "old_name": old_name, "dest": dest, "new_name": new_name})
            else:
                result = await run_in_threadpool(rename_item, path, old_name, new_name)
                results.append({"success": bool(result.get("success", True)), "op": "rename", "path": path, "old_name": old_name, "new_name": new_name})
        except ValueError as exc:
            reason = exc.args[0] if exc.args else "Opération refusée"
            logger.warning("internal-arrange-batch: op %s refusée (%s)", op, reason)
            results.append({"success": False, "op": op, "path": path, "old_name": old_name, "new_name": new_name, "dest": dest, "error": reason})
    failed = sum(1 for result in results if not result.get("success"))
    return {"results": results, "failed": failed}


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
        return await run_in_threadpool(move_item, path, name, dest)
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


@router.post("/files/move-batch")
async def move_batch(
    request: Request,
    _=Depends(require_action_guard),
    items: str = Form(...),
    dest: str = Form(""),
):
    """Move a selection in one rate-limited mutation."""
    try:
        parsed = json.loads(items)
        if not isinstance(parsed, list) or not parsed or any(not isinstance(item, dict) for item in parsed):
            raise ValueError("Sélection invalide")
        return await run_in_threadpool(move_items, parsed, dest)
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(
            status_code=403,
            detail=error_detail("path_error", "Déplacement impossible à cet emplacement.", "Vérifier le chemin et la destination"),
        )
    except Exception:
        raise HTTPException(
            status_code=500,
            detail=error_detail("move_error", "Déplacement impossible.", "Réessayer"),
        )


@router.post("/files/copy")
async def copy(
    request: Request,
    _=Depends(require_action_guard),
    path: str = Form(""),
    name: str = Form(...),
    dest: str = Form(""),
):
    """Copy a file or directory to another folder."""
    try:
        return await run_in_threadpool(copy_item, path, name, dest)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=error_detail("path_error", "Copie impossible à cet emplacement.", "Vérifier le chemin et la destination"),
        )
    except Exception:
        raise HTTPException(
            status_code=500,
            detail=error_detail("copy_error", "Copie impossible.", "Réessayer"),
        )


@router.post("/files/delete")
async def delete(
    request: Request,
    _=Depends(require_action_guard),
    path: str = Form(""),
    name: str = Form(...),
    permanent: bool = Form(False),
):
    """Delete file or directory (to trash unless permanent)."""
    try:
        return await run_in_threadpool(delete_item, path, name, permanent)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=error_detail("path_error", "Suppression impossible à cet emplacement.", "Vérifier le chemin"),
        )


@router.get("/files/trash")
async def trash_list(request: Request):
    """List trashed items."""
    _read_limiter(request)
    try:
        return {"items": await run_in_threadpool(list_trash)}
    except Exception:
        raise HTTPException(
            status_code=500,
            detail=error_detail("trash_error", "Corbeille indisponible.", "Réessayer"),
        )


@router.post("/files/trash/restore")
async def trash_restore(
    request: Request,
    _=Depends(require_action_guard),
    path: str = Form(...),
):
    """Restore a trashed item to its original location."""
    try:
        return await run_in_threadpool(restore_item, path)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=error_detail("trash_error", "Restauration impossible.", "Vérifier la corbeille"),
        )
    except Exception:
        raise HTTPException(
            status_code=500,
            detail=error_detail("trash_error", "Restauration impossible.", "Réessayer"),
        )


@router.post("/files/trash/empty")
async def trash_empty(
    request: Request,
    _=Depends(require_action_guard),
):
    """Permanently delete everything in the trash."""
    try:
        return await run_in_threadpool(empty_trash)
    except Exception:
        raise HTTPException(
            status_code=500,
            detail=error_detail("trash_error", "Vidage de la corbeille impossible.", "Réessayer"),
        )


@router.post("/files/touch")
async def touch(
    request: Request,
    _=Depends(require_action_guard),
    path: str = Form(""),
    name: str = Form(...),
):
    """Create an empty text file."""
    try:
        return await run_in_threadpool(create_text_file, path, name)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=error_detail("path_error", "Création impossible à cet emplacement.", "Vérifier le chemin"),
        )


@router.get("/files/content")
async def file_content(request: Request, path: str):
    """Read the text content of a small file."""
    _read_limiter(request)
    try:
        return await run_in_threadpool(read_text_file, path)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=error_detail("path_error", "Fichier non autorisé, introuvable ou trop volumineux.", "Vérifier le chemin"),
        )


@router.post("/files/write")
async def file_write(
    request: Request,
    _=Depends(require_action_guard),
    path: str = Form(...),
    content: str = Form(...),
):
    """Atomically overwrite a text file."""
    try:
        return await run_in_threadpool(write_text_file, path, content)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=error_detail("path_error", "Écriture impossible à cet emplacement.", "Vérifier le chemin"),
        )
    except Exception:
        raise HTTPException(
            status_code=500,
            detail=error_detail("write_error", "Écriture impossible.", "Réessayer"),
        )


@router.get("/files/properties")
async def properties(request: Request, path: str):
    """Detailed metadata for a file or directory."""
    _read_limiter(request)
    try:
        return await run_in_threadpool(get_file_properties, path)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=error_detail("path_error", "Propriétés indisponibles.", "Vérifier le chemin"),
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
        return await run_in_threadpool(get_folder_size, path, name)
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


@router.post("/files/sizes")
async def folder_sizes(
    request: Request,
    _=Depends(require_action_guard),
    paths: str = Form(...),
):
    """Compute the recursive size of several folders in one batch."""
    file_list = [p.strip() for p in paths.split("\n") if p.strip()]
    if not file_list:
        raise HTTPException(
            status_code=400,
            detail=error_detail("no_folders", "Aucun dossier sélectionné.", "Sélectionner des dossiers"),
        )
    try:
        return await run_in_threadpool(get_folder_sizes, file_list)
    except Exception:
        raise HTTPException(
            status_code=500,
            detail=error_detail("size_error", "Calcul des tailles impossible.", "Réessayer"),
        )


@router.post("/files/organize/preview")
async def organize_preview(
    request: Request,
    _=Depends(require_action_guard),
    path: str = Form(""),
):
    """Preview how the directory would be reorganized (series, movies, parasites)."""
    from ..services.media import is_qbittorrent_tree

    if is_qbittorrent_tree(path):
        raise HTTPException(
            status_code=409,
            detail=error_detail(
                "qbit_organize_coordinated",
                "Ce dossier est géré par le rangement sécurisé du Torrent Panel.",
                "Ouvrir Torrent Panel",
            ),
        )
    try:
        return await run_in_threadpool(build_organization_plan, path)
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
    from ..services.media import is_qbittorrent_tree

    if is_qbittorrent_tree(path):
        raise HTTPException(
            status_code=409,
            detail=error_detail(
                "qbit_organize_coordinated",
                "Déplacement refusé : qBittorrent doit conserver la maîtrise de ses fichiers.",
                "Utiliser le rangement du Torrent Panel",
            ),
        )
    try:
        return await run_in_threadpool(apply_organization_plan, path)
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
