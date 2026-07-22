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
:root{{--bg:#060708;--surface:rgba(255,255,255,0.045);--border:rgba(255,255,255,0.08);--text:#f5f5f7;--text2:rgba(245,245,247,0.55);--text3:rgba(245,245,247,0.42);--accent:#2997ff;--accent-bright:#4ab0ff;--accent-dim:rgba(41,151,255,0.14);--accent-glow:rgba(41,151,255,0.35);--green:#32d74b;--error:#ff453a;--amber:#ffd60a;--purple:#af52de;--radius-xl:20px;--radius-sm:10px;}}
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;padding:24px;overflow-x:hidden;-webkit-font-smoothing:antialiased;}}
.orbs{{position:fixed;inset:0;pointer-events:none;z-index:0;overflow:hidden;}}
.orb{{position:absolute;border-radius:50%;filter:blur(110px);opacity:.5;animation:drift 18s ease-in-out infinite alternate;}}
.orb.a{{width:520px;height:520px;background:#2997ff;top:-10%;left:-8%;animation-duration:22s;}}
.orb.b{{width:440px;height:440px;background:#af52de;bottom:-12%;right:-6%;animation-duration:26s;animation-delay:-6s;}}
@keyframes drift{{0%{{transform:translate(0,0) scale(1);}}100%{{transform:translate(60px,40px) scale(1.12);}}}}
.wrap{{position:relative;z-index:1;width:100%;max-width:580px;}}
.card{{background:var(--surface);backdrop-filter:blur(40px)saturate(180%);-webkit-backdrop-filter:blur(40px)saturate(180%);border:1px solid var(--border);border-radius:var(--radius-xl);padding:48px 40px 36px;text-align:center;animation:cardIn .5s cubic-bezier(.28,0,.22,1) both;box-shadow:0 20px 60px rgba(0,0,0,.55);}}
@keyframes cardIn{{0%{{opacity:0;transform:translateY(20px)scale(.97);}}100%{{opacity:1;transform:translateY(0)scale(1);}}}}
.logo{{width:46px;height:46px;border-radius:13px;background:linear-gradient(135deg,#1a5aba,#0d3f8a);display:flex;align-items:center;justify-content:center;margin:0 auto 20px;flex-shrink:0;}}
.logo svg{{width:24px;height:24px;color:#fff;}}
.fi{{width:72px;height:72px;border-radius:18px;display:flex;align-items:center;justify-content:center;margin:0 auto 16px;animation:iconFloat 3s ease-in-out infinite;}}
@keyframes iconFloat{{0%,100%{{transform:translateY(0);}}50%{{transform:translateY(-6px);}}}}
.fi-video{{background:rgba(175,82,222,.16);color:var(--purple);}}
.fi-audio{{background:rgba(50,215,75,.13);color:var(--green);}}
.fi-image{{background:rgba(41,151,255,.14);color:var(--accent);}}
.fi-pdf{{background:rgba(255,69,58,.13);color:var(--error);}}
.fi-archive{{background:rgba(255,214,10,.14);color:var(--amber);}}
.fi-file{{background:var(--accent-dim);color:var(--accent);}}
.fn{{font-size:22px;font-weight:500;word-break:break-word;margin-bottom:24px;line-height:1.3;}}
.meta-grid{{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:28px;}}
.mi{{background:rgba(255,255,255,.035);border-radius:var(--radius-sm);padding:14px 12px;}}
.mi-lbl{{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:var(--text3);margin-bottom:4px;}}
.mi-val{{font-size:15px;color:var(--text);font-weight:500;}}
.btn{{display:inline-flex;align-items:center;justify-content:center;gap:8px;width:100%;padding:16px 24px;border-radius:var(--radius-sm);background:var(--accent);color:#fff;font-weight:600;font-size:16px;text-decoration:none;border:none;cursor:pointer;transition:background .16s,transform .16s,box-shadow .16s;box-shadow:0 0 20px var(--accent-glow);}}
.btn:hover{{background:var(--accent-bright);transform:translateY(-1px);box-shadow:0 0 28px var(--accent-glow);}}
.btn:active{{transform:translateY(0);}}
.btn:focus-visible{{outline:2px solid #fff;outline-offset:3px;}}
.ft{{margin-top:20px;font-size:12px;color:var(--text3);letter-spacing:.02em;}}
input{{width:100%;padding:12px 14px;border-radius:var(--radius-sm);border:1px solid rgba(255,255,255,.08);background:rgba(255,255,255,.05);color:var(--text);font-size:15px;outline:none;transition:border .2s,box-shadow .2s;}}
input:focus{{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-dim);}}
.notice{{padding:12px 16px;border-radius:var(--radius-sm);margin-bottom:16px;font-size:14px;}}
.notice-error{{background:rgba(255,69,58,.12);border:1px solid rgba(255,69,58,.25);color:#fecaca;}}
.notice-warn{{background:rgba(255,214,10,.10);border:1px solid rgba(255,214,10,.2);color:#ffd792;}}
</style></head><body><div class="orbs"><div class="orb a"></div><div class="orb b"></div></div><div class="wrap"><div class="card">{body}</div></div></body></html>"""


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


def _notice_card(title: str, message: str, variant: str = "error") -> str:
    extra = '<div class="notice notice-' + variant + '"><strong>' + title + '</strong><br>' + message + "</div>"
    return _BASE.format(
        title=title,
        body='<div class="logo">' + _ICONS["file"] + '</div>' + extra + '<div class="ft">Cloud Panel &middot; Lien securise</div>',
    )


PASSWORD_FORM = _BASE.format(
    title="Mot de passe requis",
    body="""<div class="logo">""" + _ICONS["file"] + """</div>
<h2 style="font-size:18px;font-weight:600;margin-bottom:8px;">Mot de passe requis</h2>
<p style="color:var(--text2);font-size:14px;margin-bottom:20px;">Ce fichier est protege par un mot de passe.</p>
<form method="get" style="text-align:left">
<label style="display:block;font-size:13px;font-weight:600;color:var(--text2);margin-bottom:6px;">Mot de passe</label>
<input type="password" name="password" required placeholder="Saisir le mot de passe">
<button type="submit" class="btn" style="margin-top:16px;"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>Acceder au fichier</button>
</form><div class="ft">Cloud Panel &middot; Lien securise</div>""",
)

PASSWORD_WRONG = _BASE.format(
    title="Mot de passe incorrect",
    body="""<div class="logo">""" + _ICONS["file"] + """</div>
<div class="notice notice-error"><strong>Mot de passe incorrect</strong><br>Le mot de passe fourni est incorrect.</div>
<form method="get" style="text-align:left;margin-top:16px;">
<label style="display:block;font-size:13px;font-weight:600;color:var(--text2);margin-bottom:6px;">Mot de passe</label>
<input type="password" name="password" required placeholder="Saisir le mot de passe">
<button type="submit" class="btn" style="margin-top:16px;"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>Reessayer</button>
</form><div class="ft">Cloud Panel &middot; Lien securise</div>""",
)


_DOWNLOAD_BODY = """<div class="logo"><svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M8.5 18.25h8.25a3.25 3.25 0 0 0 .6-6.44A4.75 4.75 0 0 0 8 10.5a3.5 3.5 0 0 0 .5 6.94Z"/></svg></div>
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
