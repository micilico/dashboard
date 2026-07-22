from __future__ import annotations

import os
import time
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request, Form, Path as FPath, Query
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from ..config import PUBLIC_PREFIX
from ..services.share import (
    create_file_share_link,
    create_folder_share_link,
    create_zip_share_link,
    get_share_download_path,
    generate_qr_data_url,
    cleanup_expired_zips,
)
from ..models import get_share_link as _get_share_link, get_share_links, revoke_share_link, extend_share_link, get_stats, get_history
from .csrf_guard import require_action_guard

router = APIRouter()

SHARE_HTML = """<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Telechargement · Cloud Panel</title><style>body{{background:#07080b;color:#f5f5f7;font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;padding:16px;}}.card{{background:#101217;border:1px solid rgba(255,255,255,.09);border-radius:24px;padding:32px;max-width:480px;width:100%;text-align:center;}}.btn{{display:inline-block;padding:12px 24px;border-radius:12px;background:#0071e3;color:#fff;font-weight:600;text-decoration:none;margin-top:16px;transition:background .16s;}}.btn:hover{{background:#0077ed;}}.btn:focus-visible{{outline:2px solid #fff;outline-offset:2px;}}.meta{{color:#a7abb5;font-size:.85rem;margin-top:8px;}}.error{{color:#ff6b72;}}input{{width:100%;padding:10px 12px;border-radius:8px;border:1px solid rgba(255,255,255,.15);background:#151821;color:#f5f5f7;font-size:.95rem;margin-top:4px;box-sizing:border-box;}}</style></head><body><div class="card"><h1>{title}</h1><p>{message}</p>{extra}</div></body></html>"""

PASSWORD_FORM = """<form method="get" style="margin-top:16px;text-align:left">
<label style="display:block;margin-bottom:8px;font-size:.85rem;color:#a7abb5">Mot de passe requis
<input type="password" name="password" required style="width:100%;padding:10px 12px;border-radius:8px;border:1px solid rgba(255,255,255,.15);background:#151821;color:#f5f5f7;font-size:.95rem;margin-top:4px;box-sizing:border-box"></label>
<button type="submit" class="btn" style="width:100%">Acceder au fichier</button>
</form>"""

DOWNLOAD_PAGE_HTML = """<!doctype html>
<html lang="fr">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Telechargement · Cloud Panel</title>
    <style>
        body{{background:#07080b;color:#f5f5f7;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;padding:16px;}}
        .card{{background:#101217;border:1px solid rgba(255,255,255,.09);border-radius:24px;padding:40px;max-width:520px;width:100%;text-align:center;}}
        h1{{font-size:1.5rem;margin:0 0 24px 0;color:#f5f5f7;}}
        .file-info{{background:#151821;border-radius:16px;padding:24px;margin-bottom:24px;}}
        .filename{{font-size:1.1rem;font-weight:600;color:#f5f5f7;word-break:break-all;margin-bottom:12px;}}
        .meta{{color:#a7abb5;font-size:0.9rem;margin:6px 0;}}
        .btn{{display:inline-block;padding:14px 32px;border-radius:12px;background:#0071e3;color:#fff;font-weight:600;text-decoration:none;font-size:1rem;transition:background .16s;border:none;cursor:pointer;}}
        .btn:hover{{background:#0077ed;}}
        .btn:focus-visible{{outline:2px solid #fff;outline-offset:2px;}}
        .icon{{width:64px;height:64px;margin:0 auto 20px;color:#0071e3;}}
    </style>
</head>
<body>
    <div class="card">
        <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <path d="M12 4v10m0 0 3.5-3.5M12 14l-3.5-3.5M5 18.25h14"/>
        </svg>
        <h1>Preparer le telechargement</h1>
        <div class="file-info">
            <div class="filename">{filename}</div>
            <div class="meta">Taille : {size}</div>
            {expires}
        </div>
        <a href="{download_url}" class="btn">Telecharger le fichier</a>
    </div>
</body>
</html>"""


def _format_size(size_bytes: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


@router.post("/share/file")
async def share_file(
    request: Request,
    _=Depends(require_action_guard),
    path: str = Form(...),
    password: str = Form(""),
    expiry_days: int = Form(7),
):
    try:
        password_hash = _hash_password(password) if password else None
        result = create_file_share_link(path, password_hash, expiry_days)
        base_url = str(request.base_url).rstrip("/")
        result["qrDataUrl"] = generate_qr_data_url(result["token"], base_url)
        return result
    except ValueError as e:
        raise HTTPException(status_code=403, detail={"code": "share_error", "message": str(e), "recovery": "Verifier le chemin"})
    except Exception as e:
        raise HTTPException(status_code=500, detail={"code": "share_error", "message": str(e), "recovery": "Reessayer"})


@router.post("/share/folder")
async def share_folder(
    request: Request,
    _=Depends(require_action_guard),
    path: str = Form(...),
    password: str = Form(""),
    expiry_days: int = Form(7),
):
    try:
        password_hash = _hash_password(password) if password else None
        result = create_folder_share_link(path, password_hash, expiry_days)
        base_url = str(request.base_url).rstrip("/")
        result["qrDataUrl"] = generate_qr_data_url(result["token"], base_url)
        return result
    except ValueError as e:
        raise HTTPException(status_code=403, detail={"code": "share_error", "message": str(e), "recovery": "Verifier le chemin"})
    except Exception as e:
        raise HTTPException(status_code=500, detail={"code": "share_error", "message": str(e), "recovery": "Reessayer"})


@router.post("/share/zip")
async def share_zip(
    request: Request,
    _=Depends(require_action_guard),
    path: str = Form(...),
    password: str = Form(""),
    expiry_days: int = Form(7),
):
    try:
        password_hash = _hash_password(password) if password else None
        result = create_zip_share_link(path, password_hash, expiry_days)
        base_url = str(request.base_url).rstrip("/")
        result["qrDataUrl"] = generate_qr_data_url(result["token"], base_url)
        return result
    except ValueError as e:
        raise HTTPException(status_code=403, detail={"code": "share_error", "message": str(e), "recovery": "Verifier le chemin"})
    except Exception as e:
        raise HTTPException(status_code=500, detail={"code": "share_error", "message": str(e), "recovery": "Reessayer"})


@router.get("/links")
async def list_links(request: Request, limit: int = 50, offset: int = 0):
    try:
        items = get_share_links(limit, offset)
        return {"items": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail={"code": "db_error", "message": str(e), "recovery": "Reessayer"})


@router.post("/links/revoke")
async def revoke_link(
    request: Request,
    _=Depends(require_action_guard),
    token: str = Form(...),
):
    try:
        result = revoke_share_link(token)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail={"code": "db_error", "message": str(e), "recovery": "Reessayer"})


@router.post("/links/extend")
async def extend_link(
    request: Request,
    _=Depends(require_action_guard),
    token: str = Form(...),
    days: int = Form(7),
):
    try:
        result = extend_share_link(token, days)
        if not result.get("success"):
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": result.get("error", "Lien introuvable"), "recovery": "Verifier le token"})
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"code": "db_error", "message": str(e), "recovery": "Reessayer"})


@router.get("/download/{token}")
async def download_share(request: Request, token: str = FPath(...), dl: bool = Query(default=False), password: str | None = Query(default=None)):
    try:
        link = _get_share_link(token)
        if not link:
            return HTMLResponse(content=SHARE_HTML.format(title="Lien invalide", message="Ce lien n'existe pas.", extra=""), status_code=404)
        if link["is_revoked"]:
            return HTMLResponse(content=SHARE_HTML.format(title="Lien revoque", message="Ce lien a ete revoque.", extra=""), status_code=403)
        if link["expires_at"] and link["expires_at"] < time.time():
            return HTMLResponse(content=SHARE_HTML.format(title="Lien expire", message="Ce lien a expire.", extra=""), status_code=403)

        pw_hash = link.get("password_hash")
        if pw_hash:
            if not password:
                return HTMLResponse(content=SHARE_HTML.format(
                    title="Mot de passe requis",
                    message="Ce fichier est protege par un mot de passe.",
                    extra=PASSWORD_FORM,
                ), status_code=401)
            if not verify_password(password, pw_hash):
                return HTMLResponse(content=SHARE_HTML.format(
                    title="Mot de passe incorrect",
                    message="Le mot de passe fourni est incorrect.",
                    extra=PASSWORD_FORM,
                ), status_code=403)

        if dl:
            file_path, filename = get_share_download_path(token)
            return FileResponse(file_path, filename=filename, media_type="application/octet-stream")

        file_path, filename = get_share_download_path(token, increment=False)
        size_formatted = _format_size(os.path.getsize(file_path))
        expires_at = link.get("expires_at")
        expires_str = ""
        if expires_at:
            expires_str = '<div class="meta">Expire le : ' + datetime.fromtimestamp(expires_at).strftime("%d/%m/%Y a %H:%M") + "</div>"

        dl_url = str(request.url)
        sep = "&" if "?" in dl_url else "?"
        dl_url += f"{sep}dl=1"
        if password:
            dl_url += f"&password={password}"

        page = DOWNLOAD_PAGE_HTML.format(filename=filename, size=size_formatted, expires=expires_str, download_url=dl_url)
        return HTMLResponse(content=page)
    except ValueError as e:
        html = SHARE_HTML.format(title="Lien invalide", message=str(e), extra="")
        return HTMLResponse(content=html, status_code=404)
    except Exception as e:
        html = SHARE_HTML.format(title="Erreur", message=str(e), extra="")
        return HTMLResponse(content=html, status_code=500)


@router.get("/stats")
async def stats(request: Request):
    try:
        return get_stats()
    except Exception as e:
        raise HTTPException(status_code=500, detail={"code": "db_error", "message": str(e), "recovery": "Reessayer"})


@router.get("/history/data")
async def history_data(request: Request, limit: int = 50, offset: int = 0):
    try:
        items = get_history(limit, offset)
        return {"items": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail={"code": "db_error", "message": str(e), "recovery": "Reessayer"})


def _hash_password(password: str) -> str:
    import hashlib
    salt = os.urandom(16).hex()
    return salt + ":" + hashlib.sha256((salt + password).encode()).hexdigest()


def verify_password(password: str, password_hash: str) -> bool:
    import hashlib
    if ":" not in password_hash:
        return False
    salt, hsh = password_hash.split(":", 1)
    return hsh == hashlib.sha256((salt + password).encode()).hexdigest()
