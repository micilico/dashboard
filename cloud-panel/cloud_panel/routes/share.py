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

_BASE = """<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} · Cloud Panel</title><style>
:root{{--bg:#07080b;--surface:#101217;--surface-2:#151821;--border:rgba(255,255,255,0.09);--text:#f5f5f7;--muted:#a7abb5;--text-subtle:#7E8491;--accent:#0071e3;--accent-hover:#0077ed;--accent-soft:rgba(0,113,227,0.12);--success:#5ee6a8;--danger:#ff6b72;--radius-card:18px;--radius-control:12px;--ease-standard:cubic-bezier(0.28,0,0.22,1);--font-body:'Inter Variable','Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;--font-display:'Inter Tight','Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;}}
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{background:var(--bg);color:var(--text);font-family:var(--font-body);display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;padding:24px;-webkit-font-smoothing:antialiased;line-height:1.47;}}
.wrap{{width:100%;max-width:480px;}}
.card{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-card);padding:40px 32px 32px;text-align:center;box-shadow:0 12px 32px rgba(0,0,0,0.08);}}
.logo{{width:48px;height:48px;margin:0 auto 20px;}}
.logo svg{{width:100%;height:100%;}}
.fi{{width:64px;height:64px;border-radius:14px;display:flex;align-items:center;justify-content:center;margin:0 auto 16px;background:var(--surface-2);color:var(--muted);}}
.fi svg{{width:28px;height:28px;}}
.fn{{font-family:var(--font-display);font-size:22px;font-weight:600;letter-spacing:-0.015em;word-break:break-word;margin-bottom:24px;line-height:1.25;}}
.meta-grid{{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:24px;}}
.mi{{background:var(--surface-2);border-radius:var(--radius-control);padding:12px;}}
.mi-lbl{{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.08em;color:var(--text-subtle);margin-bottom:4px;}}
.mi-val{{font-size:15px;color:var(--text);font-weight:600;font-variant-numeric:tabular-nums;}}
.btn{{display:inline-flex;align-items:center;justify-content:center;gap:8px;width:100%;min-height:48px;padding:12px 24px;border-radius:var(--radius-control);background:var(--accent);color:#fff;font-weight:600;font-size:15px;font-family:var(--font-body);text-decoration:none;border:none;cursor:pointer;transition:background .15s var(--ease-standard),transform .15s var(--ease-standard);}}
.btn:hover{{background:var(--accent-hover);}}
.btn:active{{transform:translateY(1px);}}
.btn:focus-visible{{outline:3px solid var(--accent);outline-offset:3px;}}
.ft{{margin-top:20px;font-size:12px;color:var(--text-subtle);letter-spacing:0.04em;}}
input{{width:100%;min-height:44px;padding:10px 12px;border-radius:var(--radius-control);border:1px solid var(--border);background:var(--surface);color:var(--text);font-family:var(--font-body);font-size:14px;outline:none;transition:border .15s var(--ease-standard);}}
input:focus{{border-color:var(--accent);}}
.notice{{padding:12px 14px;border-radius:var(--radius-control);margin-bottom:16px;font-size:14px;background:var(--surface-2);border:1px solid var(--border);color:var(--muted);}}
.notice-error{{background:rgba(255,107,114,0.12);border-color:rgba(239,68,68,0.25);color:#fecaca;}}
.notice-warn{{background:rgba(244,189,98,0.11);border-color:rgba(245,158,11,0.25);color:#ffd792;}}
</style></head><body><div class="wrap"><div class="card">{body}</div></div></body></html>"""


def _get_file_category(filename: str) -> str:
    ext = (filename or "").rsplit(".", 1)[-1].lower()
    if ext in ("mp4", "mkv", "avi", "mov", "webm", "m4v"):
        return "video"
    if ext in ("mp3", "flac", "wav", "ogg", "m4a", "aac", "opus"):
        return "audio"
    if ext in ("jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "ico"):
        return "image"
    if ext in ("pdf",):
        return "pdf"
    if ext in ("zip", "rar", "7z", "tar", "gz", "bz2", "xz"):
        return "archive"
    return "file"


def _format_size(size_bytes: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


_ICONS = {
    "video": """<svg viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="8.25"/><path d="M10 9.5v5l5-2.5z"/></svg>""",
    "audio": """<svg viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="8.25"/><path d="M12 8v8M8 10.5v3M16 10.5v3"/></svg>""",
    "image": """<svg viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="4" y="4" width="16" height="16" rx="3"/><circle cx="9" cy="9" r="2"/><path d="M4 16l4-4 3 3 3-4 6 5"/></svg>""",
    "pdf": """<svg viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 9h8M8 13h5M8 17h8"/><path d="M15 3v4h4"/></svg>""",
    "archive": """<svg viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M5 8.5h14M5 8.5A2 2 0 0 1 3 6.5v-2A2 2 0 0 1 5 2.5h14a2 2 0 0 1 2 2v2a2 2 0 0 1-2 2M5 8.5v9a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-9"/></svg>""",
    "file": """<svg viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M7.75 3.75h4.5l6 6v10.5a1 1 0 0 1-1 1H7.75a1 1 0 0 1-1-1V4.75a1 1 0 0 1 1-1z"/><path d="M12.25 3.75v6h6"/></svg>""",
}

_SLICE_LOGO = """<svg viewBox="0 0 64 64" fill="none" aria-hidden="true"><polygon points="18,14 41,14 50,30 27,30" stroke="currentColor" stroke-width="2.5" stroke-linejoin="round" opacity="0.7"/><polygon points="14,34 37,34 46,50 23,50" stroke="currentColor" stroke-width="2.5" stroke-linejoin="round"/><polygon points="27,30 41,30 37,34 23,34" fill="currentColor" opacity="0.15"/></svg>"""


def _notice_card(title: str, message: str, variant: str = "error") -> str:
    extra = '<div class="notice notice-' + variant + '"><strong>' + title + '</strong><br>' + message + "</div>"
    return _BASE.format(
        title=title,
        body='<div class="logo">' + _SLICE_LOGO + '</div>' + extra + '<div class="ft">Cloud Panel &middot; Lien securise</div>',
    )


PASSWORD_FORM = _BASE.format(
    title="Mot de passe requis",
    body="""<div class="logo">""" + _SLICE_LOGO + """</div>
<h2 style="font-size:18px;font-weight:600;letter-spacing:-0.015em;margin-bottom:8px;font-family:var(--font-display)">Mot de passe requis</h2>
<p style="color:var(--muted);font-size:14px;margin-bottom:20px;">Ce fichier est protege par un mot de passe.</p>
<form method="get" style="text-align:left">
<label style="display:block;font-size:13px;font-weight:600;color:var(--muted);margin-bottom:6px;">Mot de passe</label>
<input type="password" name="password" required placeholder="Saisir le mot de passe">
<button type="submit" class="btn" style="margin-top:16px;"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>Acceder au fichier</button>
</form><div class="ft">Cloud Panel &middot; Lien securise</div>""",
)

PASSWORD_WRONG = _BASE.format(
    title="Mot de passe incorrect",
    body="""<div class="logo">""" + _SLICE_LOGO + """</div>
<div class="notice notice-error"><strong>Mot de passe incorrect</strong><br>Le mot de passe fourni est incorrect.</div>
<form method="get" style="text-align:left;margin-top:16px;">
<label style="display:block;font-size:13px;font-weight:600;color:var(--muted);margin-bottom:6px;">Mot de passe</label>
<input type="password" name="password" required placeholder="Saisir le mot de passe">
<button type="submit" class="btn" style="margin-top:16px;"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>Reessayer</button>
</form><div class="ft">Cloud Panel &middot; Lien securise</div>""",
)


_DOWNLOAD_BODY = """<div class="logo">""" + _SLICE_LOGO + """</div>
<div class="fi fi-{category}">{icon}</div>
<div class="fn">{filename}</div>
<div class="meta-grid">
<div class="mi"><div class="mi-lbl">Taille</div><div class="mi-val">{size}</div></div>
<div class="mi"><div class="mi-lbl">Telechargements</div><div class="mi-val">{dl_count}</div></div>
{expires_row}
<div class="mi"><div class="mi-lbl">Type</div><div class="mi-val">{file_type}</div></div>
</div>
<a href="{dl_url}" class="btn"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 4v10m0 0 3.5-3.5M12 14l-3.5-3.5M5 18.25h14"/></svg>Telecharger le fichier</a>
<div class="ft">Cloud Panel &middot; Lien securise</div>"""


def _download_page(filename: str, size: str, category: str, file_type: str, download_count: int, expires: str, dl_url: str) -> str:
    icon_svg = _ICONS.get(category, _ICONS["file"])
    if expires:
        expires_row = '<div class="mi"><div class="mi-lbl">Expire le</div><div class="mi-val">' + expires + "</div></div>"
    else:
        expires_row = '<div class="mi"><div class="mi-lbl">Expiration</div><div class="mi-val">Aucune</div></div>'
    return _BASE.format(
        title=filename,
        body=_DOWNLOAD_BODY.format(
            category=category, icon=icon_svg, filename=filename,
            size=size, dl_count=str(download_count),
            expires_row=expires_row, file_type=file_type,
            dl_url=dl_url,
        ),
    )


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
            return HTMLResponse(content=_notice_card("Lien invalide", "Ce lien n'existe pas.", "error"), status_code=404)
        if link["is_revoked"]:
            return HTMLResponse(content=_notice_card("Lien revoque", "Ce lien a ete revoque.", "warn"), status_code=403)
        if link["expires_at"] and link["expires_at"] < time.time():
            return HTMLResponse(content=_notice_card("Lien expire", "Ce lien a expire.", "error"), status_code=403)

        pw_hash = link.get("password_hash")
        if pw_hash:
            if not password:
                return HTMLResponse(content=PASSWORD_FORM, status_code=401)
            if not verify_password(password, pw_hash):
                return HTMLResponse(content=PASSWORD_WRONG, status_code=403)

        if dl:
            file_path, filename = get_share_download_path(token)
            return FileResponse(file_path, filename=filename, media_type="application/octet-stream")

        file_path, filename = get_share_download_path(token, increment=False)
        size_formatted = _format_size(os.path.getsize(file_path))
        category = _get_file_category(filename)

        is_dir = link.get("is_dir", False)
        is_zip = link.get("is_zip", False)
        if is_dir:
            file_type = "Dossier"
        elif is_zip:
            file_type = "Archive ZIP"
        else:
            mapping = {"video": "Video", "audio": "Audio", "image": "Image", "pdf": "Document PDF", "archive": "Archive"}
            file_type = mapping.get(category, "Fichier")

        download_count = link.get("download_count", 0)
        expires_at = link.get("expires_at")
        expires_str = ""
        if expires_at:
            expires_str = datetime.fromtimestamp(expires_at).strftime("%d/%m/%Y a %H:%M")

        dl_url = str(request.url)
        sep = "&" if "?" in dl_url else "?"
        dl_url += f"{sep}dl=1"
        if password:
            dl_url += f"&password={password}"

        page = _download_page(filename, size_formatted, category, file_type, download_count, expires_str, dl_url)
        return HTMLResponse(content=page)
    except ValueError as e:
        return HTMLResponse(content=_notice_card("Lien invalide", str(e), "error"), status_code=404)
    except Exception as e:
        return HTMLResponse(content=_notice_card("Erreur", str(e), "error"), status_code=500)


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
