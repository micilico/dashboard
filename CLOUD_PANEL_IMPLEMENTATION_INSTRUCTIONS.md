# CLOUD_PANEL_IMPLEMENTATION_INSTRUCTIONS.md

## Vue d'ensemble

Créer un nouveau panel `cloud-panel` pour le dashboard permettant de gérer les fichiers sur le montage rclone `/mnt/ultra-media` avec upload/download bidirectionnel (sans limite de taille), en réutilisant la logique de `gofile-manager`.

## Objectifs fonctionnels

1. **Navigation de fichiers** : Explorer l'arborescence `/mnt/ultra-media` avec pagination
2. **Upload de fichiers** : Streaming chunk-by-chunk sans limite de taille, écriture atomique via `.tmp` + `os.rename()`
3. **Téléchargement de fichiers** : Téléchargement direct depuis le navigateur
4. **Opérations sur fichiers** : Créer dossiers, renommer, supprimer
5. **Rafraîchissement** : Invalider le cache et recharger le contenu
6. **Sécurité** : Protection CSRF, rate limiting, prévention path traversal

## Architecture technique

### Stack

- **Backend** : Python 3.12, FastAPI, uvicorn
- **Frontend** : HTML/CSS/JavaScript natifs (pas de framework)
- **Package partagé** : `dashboard-common` pour CSRF, rate limiter, CSP, CSS tokens
- **Docker** : Image `python:3.12-slim`, `USER nobody`, `network_mode: host`
- **Reverse proxy** : Caddy, route `/cloud-panel/*` vers `127.0.0.1:3130`

### Flux réseau

```
Internet → Caddy (basic_auth) → cloud-panel (127.0.0.1:3130)
                                      ↓
                              /mnt/ultra-media (rclone FUSE, read-write)
```

## Structure des fichiers

```
dashboard/
├── cloud-panel/
│   ├── Dockerfile
│   ├── build.py
│   ├── requirements.txt
│   ├── .env.example
│   └── cloud_panel/
│       ├── __init__.py
│       ├── __main__.py
│       ├── main.py              # FastAPI app, middleware, routes statiques
│       ├── config.py            # Configuration via env
│       ├── security.py          # resolve_path_within(), path traversal protection
│       ├── storage.py           # Logique fichiers (scandir, upload, download, opérations)
│       ├── routes/
│       │   ├── __init__.py
│       │   └── files.py         # Endpoints API (list, upload, download, mkdir, rename, delete)
│       └── static/
│           ├── index.html       # App principale
│           ├── app.js           # Logique principale
│           ├── app.css          # Styles spécifiques
│           ├── css/
│           │   └── files.css    # Modules CSS (optionnel)
│           └── dist/            # Bundles générés par build.py
│               ├── app.min.css
│               └── app.min.js
```

## Configuration Docker

### docker-compose.yml

Ajouter le service `cloud-panel` :

```yaml
  cloud-panel:
    build:
      context: .
      dockerfile: ./cloud-panel/Dockerfile
    container_name: cloud-panel
    restart: unless-stopped
    network_mode: "host"
    env_file:
      - path: ./cloud-panel/.env
        required: false
    environment:
      CLOUD_PANEL_HOST: 127.0.0.1
      CLOUD_PANEL_PORT: ${CLOUD_PANEL_PORT:-3130}
      CLOUD_PANEL_MOUNT_PATH: /mnt/ultra-media
    volumes:
      - /mnt/ultra-media:/mnt/ultra-media:rw
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3130/healthz', timeout=4)"]
      interval: 30s
      timeout: 5s
      retries: 5
      start_period: 20s
```

**Important** : Le volume doit être monté en **read-write** (`:rw`) contrairement à `gofile-manager` qui était read-only.

### Dockerfile

```dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY cloud-panel/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common ./common
RUN pip install --no-cache-dir ./common

COPY cloud-panel/cloud_panel ./cloud_panel
COPY cloud-panel/build.py ./build.py
RUN python build.py

USER nobody

CMD ["python", "-m", "cloud_panel"]
```

### requirements.txt

```
fastapi==0.139.2
httpx==0.28.1
starlette==1.3.1
uvicorn[standard]==0.35.0
sentry-sdk[fastapi]==2.42.0
python-multipart==0.0.9
```

### .env.example

```
# Cloud Panel listener
CLOUD_PANEL_PORT=3130

# Mount path
CLOUD_PANEL_MOUNT_PATH=/mnt/ultra-media

# Rate limiting
CLOUD_PANEL_RATE_LIMIT_CALLS=40
CLOUD_PANEL_RATE_LIMIT_SECONDS=60

# CSRF
CLOUD_PANEL_CSRF_TOKEN_TTL_SECONDS=43200

# Sentry (optional)
SENTRY_DSN=
SENTRY_ENVIRONMENT=production
```

## Configuration Caddy

### caddy/dashboard.conf

Ajouter la route `/cloud-panel/*` :

```caddyfile
  redir /cloud-panel /cloud-panel/ 308

  handle /cloud-panel/* {
    reverse_proxy 127.0.0.1:3130
  }
```

### .env.example (global)

Ajouter :

```
# Cloud Panel listener
CLOUD_PANEL_PORT=3130
```

## Backend Python

### cloud_panel/config.py

```python
from __future__ import annotations

import os
from pathlib import Path

STATIC_DIR = Path(__file__).parent / "static"
PUBLIC_PREFIX = os.getenv("CLOUD_PANEL_PUBLIC_PREFIX", "/cloud-panel").rstrip("/")
MOUNT_PATH = os.getenv("CLOUD_PANEL_MOUNT_PATH", "/mnt/ultra-media")
CSRF_COOKIE = "cloud_panel_csrf"
CSRF_HEADER = "X-Cloud-Panel-CSRF"
MAX_RATE_KEYS = int(os.getenv("CLOUD_PANEL_RATE_LIMIT_KEYS", "2048"))
RATE_LIMIT_CALLS = int(os.getenv("CLOUD_PANEL_RATE_LIMIT_CALLS", "40"))
RATE_LIMIT_SECONDS = int(os.getenv("CLOUD_PANEL_RATE_LIMIT_SECONDS", "60"))
CSRF_TOKEN_TTL_SECONDS = int(os.getenv("CLOUD_PANEL_CSRF_TOKEN_TTL_SECONDS", "43200"))
MAX_CSRF_TOKENS = int(os.getenv("CLOUD_PANEL_CSRF_TOKEN_KEYS", "128"))
TRUSTED_PROXY_IPS = {
    item.strip()
    for item in os.getenv("CLOUD_PANEL_TRUSTED_PROXY_IPS", "127.0.0.1,::1").split(",")
    if item.strip()
}
UPLOAD_CHUNK_SIZE = int(os.getenv("CLOUD_PANEL_UPLOAD_CHUNK_SIZE", str(1024 * 1024)))  # 1 MB
SCANDIR_CACHE_TTL = int(os.getenv("CLOUD_PANEL_SCANDIR_CACHE_TTL", "10"))
```

### cloud_panel/security.py

Réutiliser la logique de `gofile-manager/app/security.py` :

```python
from __future__ import annotations

import os
import re

_SAFE_ID_RE = re.compile(r'\A[A-Za-z0-9_-]{1,128}\Z')


def resolve_path_within(base_dir: str, relative_path: str, *, must_exist: bool = True) -> str:
    """Resolve a user-controlled path while containing symlinks inside base_dir."""
    if not isinstance(relative_path, str) or '\x00' in relative_path:
        raise ValueError('Chemin invalide')
    base = os.path.realpath(base_dir)
    candidate = os.path.realpath(os.path.join(base, relative_path))
    try:
        contained = os.path.commonpath((base, candidate)) == base
    except ValueError:
        contained = False
    if not contained or (must_exist and not os.path.exists(candidate)):
        raise ValueError('Chemin hors du repertoire autorise')
    return candidate


def validate_public_id(value: str, name: str = 'identifiant') -> str:
    if not isinstance(value, str) or not _SAFE_ID_RE.fullmatch(value):
        raise ValueError(f'{name} invalide')
    return value
```

### cloud_panel/storage.py

Logique de gestion des fichiers inspirée de `gofile-manager/app/routes/files.py` :

```python
from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path

from .config import MOUNT_PATH, SCANDIR_CACHE_TTL, UPLOAD_CHUNK_SIZE
from .security import resolve_path_within

logger = logging.getLogger(__name__)

_scandir_cache: dict[tuple[str, float], tuple[float, list[dict]]] = {}


def get_cached_scandir(path: str, ttl: int = SCANDIR_CACHE_TTL) -> list[dict]:
    """Cache scandir results for TTL seconds."""
    now = time.time()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0
    cache_key = (path, mtime)
    if cache_key in _scandir_cache:
        cached_time, cached_result = _scandir_cache[cache_key]
        if now - cached_time < ttl:
            return cached_result
    result = []
    try:
        for entry in os.scandir(path):
            stat = entry.stat()
            is_dir = entry.is_dir()
            size_bytes = 0 if is_dir else stat.st_size
            result.append({
                'name': entry.name,
                'is_dir': is_dir,
                'path': os.path.relpath(entry.path, MOUNT_PATH),
                'size': '' if is_dir else format_size(size_bytes),
                'size_bytes': size_bytes,
                'modified': int(stat.st_mtime),
            })
    except Exception:
        logger.warning('scandir error for %s', cache_key, exc_info=True)
    result.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
    _scandir_cache[cache_key] = (now, result)
    return result


def clear_scandir_cache() -> None:
    _scandir_cache.clear()


def format_size(size_bytes: int) -> str:
    """Format byte size to human-readable string."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


def list_directory(relative_path: str = '') -> dict:
    """List directory contents with metadata."""
    target_dir = resolve_path_within(MOUNT_PATH, relative_path)
    if not os.path.isdir(target_dir):
        raise ValueError('Dossier introuvable')
    items = get_cached_scandir(target_dir)
    try:
        usage = shutil.disk_usage(MOUNT_PATH)
        disk_used = format_size(usage.used)
        disk_total = format_size(usage.total)
        disk_percent = round(usage.used / usage.total * 100, 1)
    except Exception:
        disk_used = disk_total = 'N/A'
        disk_percent = 0
    return {
        'items': items,
        'current_path': relative_path,
        'disk_used': disk_used,
        'disk_total': disk_total,
        'disk_percent': disk_percent,
    }


async def upload_file_streaming(
    relative_path: str,
    filename: str,
    stream,
) -> dict:
    """Upload file with streaming chunk-by-chunk, write to .tmp then atomic rename."""
    target_dir = resolve_path_within(MOUNT_PATH, relative_path, must_exist=True)
    if not os.path.isdir(target_dir):
        raise ValueError('Dossier destination introuvable')
    
    final_path = os.path.join(target_dir, filename)
    tmp_path = final_path + '.tmp'
    
    try:
        total_size = 0
        with open(tmp_path, 'wb') as f:
            async for chunk in stream:
                f.write(chunk)
                total_size += len(chunk)
        
        os.rename(tmp_path, final_path)
        clear_scandir_cache()
        
        return {
            'success': True,
            'filename': filename,
            'size': format_size(total_size),
            'path': os.path.relpath(final_path, MOUNT_PATH),
        }
    except Exception as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        logger.exception('Upload failed')
        raise


def download_file(relative_path: str) -> str:
    """Return absolute path for file download."""
    file_path = resolve_path_within(MOUNT_PATH, relative_path, must_exist=True)
    if not os.path.isfile(file_path):
        raise ValueError('Fichier introuvable')
    return file_path


def create_directory(relative_path: str, dirname: str) -> dict:
    """Create a new directory."""
    parent_dir = resolve_path_within(MOUNT_PATH, relative_path, must_exist=True)
    if not os.path.isdir(parent_dir):
        raise ValueError('Dossier parent introuvable')
    
    new_dir = os.path.join(parent_dir, dirname)
    if os.path.exists(new_dir):
        raise ValueError('Ce dossier existe deja')
    
    os.makedirs(new_dir, exist_ok=True)
    clear_scandir_cache()
    
    return {
        'success': True,
        'name': dirname,
        'path': os.path.relpath(new_dir, MOUNT_PATH),
    }


def rename_item(relative_path: str, old_name: str, new_name: str) -> dict:
    """Rename a file or directory."""
    parent_dir = resolve_path_within(MOUNT_PATH, relative_path, must_exist=True)
    old_path = os.path.join(parent_dir, old_name)
    new_path = os.path.join(parent_dir, new_name)
    
    if not os.path.exists(old_path):
        raise ValueError('Element introuvable')
    if os.path.exists(new_path):
        raise ValueError('Ce nom existe deja')
    
    os.rename(old_path, new_path)
    clear_scandir_cache()
    
    return {
        'success': True,
        'old_name': old_name,
        'new_name': new_name,
        'path': os.path.relpath(new_path, MOUNT_PATH),
    }


def delete_item(relative_path: str, name: str) -> dict:
    """Delete a file or directory."""
    parent_dir = resolve_path_within(MOUNT_PATH, relative_path, must_exist=True)
    target = os.path.join(parent_dir, name)
    
    if not os.path.exists(target):
        raise ValueError('Element introuvable')
    
    if os.path.isdir(target):
        shutil.rmtree(target)
    else:
        os.remove(target)
    
    clear_scandir_cache()
    
    return {
        'success': True,
        'name': name,
        'path': os.path.relpath(target, MOUNT_PATH),
    }
```

### cloud_panel/routes/files.py

```python
from __future__ import annotations

import os
from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse

from ..config import MOUNT_PATH
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
```

### cloud_panel/main.py

```python
from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

_sys_path_root = Path(__file__).resolve().parents[2]
if str(_sys_path_root) not in sys.path:
    sys.path.insert(0, str(_sys_path_root))

from common import build_csp, RateLimiter
from common.monitoring import init_sentry
from common.csrf import cleanup_csrf_tokens as _common_cleanup
from common.csrf import csrf_cookie_matches, csrf_token_is_valid

from .config import (
    PUBLIC_PREFIX,
    CSRF_COOKIE,
    CSRF_TOKEN_TTL_SECONDS,
    MAX_CSRF_TOKENS,
    MAX_RATE_KEYS,
    RATE_LIMIT_CALLS,
    RATE_LIMIT_SECONDS,
    STATIC_DIR,
    TRUSTED_PROXY_IPS,
)
from .routes.files import router as files_router

from logging import basicConfig, getLogger

basicConfig(level=os.getenv("CLOUD_PANEL_LOG_LEVEL", "INFO"))
logger = getLogger("cloud_panel")

init_sentry(os.getenv("SENTRY_DSN", ""), os.getenv("SENTRY_ENVIRONMENT", "production"))

app = FastAPI(title="Cloud Panel", docs_url=None, redoc_url=None, openapi_url=None)

_COMMON_CSS_DIR = Path(sys.modules["common"].__file__).resolve().parent / "css"
app.mount("/common/css", StaticFiles(directory=str(_COMMON_CSS_DIR)), name="common-css")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
if PUBLIC_PREFIX:
    app.mount(f"{PUBLIC_PREFIX}/static", StaticFiles(directory=STATIC_DIR), name="prefixed-static")

app.state.csrf_tokens = {}
app.state.action_limiter = RateLimiter(
    max_calls=RATE_LIMIT_CALLS,
    period_seconds=RATE_LIMIT_SECONDS,
    max_keys=MAX_RATE_KEYS,
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = build_csp()
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "accelerometer=(), autoplay=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    if "/api/" in request.url.path:
        response.headers["Cache-Control"] = "no-store"
    return response


def cleanup_csrf_tokens(app_instance, now=None):
    return _common_cleanup(app_instance, CSRF_TOKEN_TTL_SECONDS, MAX_CSRF_TOKENS, now)


def client_key(request: Request) -> str:
    from common.csrf import client_key as _ck
    return _ck(request, TRUSTED_PROXY_IPS)


def require_action_guard(request: Request, x_cloud_panel_csrf: str | None = None):
    from fastapi import Header
    if x_cloud_panel_csrf is None:
        x_cloud_panel_csrf = request.headers.get("X-Cloud-Panel-CSRF")
    if (
        not x_cloud_panel_csrf
        or not csrf_cookie_matches(request, x_cloud_panel_csrf, CSRF_COOKIE)
        or not csrf_token_is_valid(request.app, x_cloud_panel_csrf, CSRF_TOKEN_TTL_SECONDS, MAX_CSRF_TOKENS)
    ):
        raise HTTPException(
            status_code=403,
            detail={"code": "csrf_expired", "message": "Session de protection expiree.", "recovery": "Actualiser la session"},
        )
    if not request.app.state.action_limiter.allow(client_key(request)):
        raise HTTPException(
            status_code=429,
            detail={"code": "rate_limited", "message": "Trop d'actions en peu de temps.", "recovery": "Reessayer"},
        )


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/config.js")
async def config_js() -> PlainTextResponse:
    return PlainTextResponse(
        "\n".join([
            "window.__CLOUD_PANEL_CONFIG__ = {",
            f'  publicPrefix: "{PUBLIC_PREFIX or ""}",',
            "};",
        ]),
        media_type="application/javascript",
    )


if PUBLIC_PREFIX:
    @app.get(PUBLIC_PREFIX)
    async def prefixed_index_redirect() -> FileResponse:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=f"{PUBLIC_PREFIX}/", status_code=308)

    @app.get(f"{PUBLIC_PREFIX}/")
    async def prefixed_index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get(f"{PUBLIC_PREFIX}/config.js")
    async def prefixed_config_js() -> PlainTextResponse:
        return await config_js()


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


if PUBLIC_PREFIX:
    @app.get(f"{PUBLIC_PREFIX}/healthz")
    async def prefixed_healthz() -> dict[str, str]:
        return await healthz()


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    return {"status": "ready"}


if PUBLIC_PREFIX:
    @app.get(f"{PUBLIC_PREFIX}/readyz")
    async def prefixed_readyz() -> dict[str, str]:
        return await readyz()


app.include_router(files_router, prefix="/api")
if PUBLIC_PREFIX:
    app.include_router(files_router, prefix=f"{PUBLIC_PREFIX}/api")
```

### cloud_panel/__main__.py

```python
from __future__ import annotations

import os
import uvicorn

from .config import PUBLIC_PREFIX

if __name__ == "__main__":
    host = os.getenv("CLOUD_PANEL_HOST", "127.0.0.1")
    port = int(os.getenv("CLOUD_PANEL_PORT", "3130"))
    uvicorn.run(
        "cloud_panel.main:app",
        host=host,
        port=port,
        reload=False,
        log_level=os.getenv("CLOUD_PANEL_LOG_LEVEL", "info").lower(),
    )
```

### cloud_panel/__init__.py

```python
"""Cloud Panel – File manager for rclone mount."""
```

### build.py

```python
#!/usr/bin/env python3
"""Build script: concatenate CSS and JS into dist/."""
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT.parent))
from common import resolve_css_imports

STATIC = ROOT / "cloud_panel" / "static"
COMMON = ROOT / "common" if (ROOT / "common").exists() else ROOT.parent / "common"
DIST = STATIC / "dist"

DIST.mkdir(parents=True, exist_ok=True)

# CSS
css_content = resolve_css_imports(COMMON / "css" / "index.css")

app_css = STATIC / "app.css"
if app_css.exists():
    css_content += "\n" + resolve_css_imports(app_css)

# CSS modules
css_module_dir = STATIC / "css"
if css_module_dir.exists():
    for module in sorted(css_module_dir.glob("*.css")):
        css_content += "\n" + resolve_css_imports(module)

(DIST / "app.min.css").write_text(css_content, encoding="utf-8")

# JS
js_files = [
    COMMON / "js" / "api.js",
    STATIC / "app.js",
]
js_content = "\n".join(
    f.read_text(encoding="utf-8") for f in js_files if f.exists()
)
(DIST / "app.min.js").write_text(js_content, encoding="utf-8")

print(f"Build complete: {DIST}/app.min.css + app.min.js")
```

## Frontend HTML/CSS/JS

### static/index.html

Structure HTML de base avec :
- Header avec titre "Cloud Panel"
- Breadcrumb de navigation
- Barre d'actions (upload, nouveau dossier, rafraîchir)
- Liste des fichiers/dossiers avec pagination
- Modales pour upload, création de dossier, renommage, suppression
- Indicateur de stockage disque

Utiliser les composants du design system partagé (`common/css/components/`).

### static/app.js

Logique JavaScript native :
- Navigation entre dossiers
- Upload de fichiers avec barre de progression
- Création de dossiers
- Renommage et suppression
- Rafraîchissement du cache
- Gestion CSRF (cookie + header)
- Pagination

### static/app.css

Styles spécifiques au cloud-panel utilisant les tokens CSS partagés.

## Sécurité

### Protection CSRF

- Cookie `cloud_panel_csrf` (httponly, samesite=strict)
- Header `X-Cloud-Panel-CSRF` sur toutes les mutations
- TTL configurable (défaut 12h)
- Nettoyage périodique des tokens expirés

### Rate Limiting

- Limiteur par clé client (IP + User-Agent)
- Défaut : 40 appels / 60 secondes
- Retourne HTTP 429 avec message structuré

### Path Traversal

- `resolve_path_within()` garantit que tous les chemins restent dans `/mnt/ultra-media`
- Résolution des symlinks avec `os.path.realpath()`
- Validation stricte avant toute opération

### Headers de sécurité

- CSP stricte : `default-src 'self'`
- HSTS, X-Content-Type-Options, Referrer-Policy, Permissions-Policy
- Pas de inline, pas de CDN, pas de frame

## Upload streaming

### Principe

1. Frontend envoie le fichier chunk par chunk (1 MB par défaut)
2. Backend écrit chaque chunk dans un fichier `.tmp`
3. Une fois tous les chunks reçus, `os.rename()` atomique vers le nom final
4. En cas d'erreur, suppression du fichier `.tmp`

### Avantages

- Pas de limite de taille (streaming)
- Pas de consommation mémoire excessive
- Atomicité (pas de fichier partiel visible)
- Reprise sur erreur possible (futur)

## Design system

### Tokens CSS

Utiliser exclusivement les tokens de `common/css/tokens.css` :
- Couleurs : `--color-bg`, `--color-surface`, `--color-accent`, etc.
- Espacements : `--space-4`, `--space-8`, `--space-16`, etc.
- Rayons : `--radius-sm`, `--radius-md`, `--radius-lg`
- Ombres : `--shadow-sm`, `--shadow-md`

### Composants

Réutiliser les composants de `common/css/components/` :
- Boutons (`.btn`, `.btn-primary`, `.btn-danger`)
- Cartes (`.card`)
- Dialogues (`.dialog`)
- Formulaires (`.form-group`, `.input`)
- Navigation (`.nav`)
- Tableaux (`.table`)

### Responsive

Breakpoints : 375, 768, 1024, 1440, 1600 px.

## Tests

### Backend

```bash
pytest cloud-panel/tests/test_backend.py
```

Tests à implémenter :
- List directory
- Upload streaming
- Download file
- Create directory
- Rename item
- Delete item
- Path traversal protection
- CSRF validation
- Rate limiting

### Frontend

```bash
node cloud-panel/tests/frontend_logic.test.js
```

Tests à implémenter :
- Navigation
- Upload progress
- Modal interactions
- Error handling

## Commandes

### Build

```bash
python cloud-panel/build.py
```

### Docker

```bash
docker compose build cloud-panel
docker compose up -d
```

### Vérification

```bash
docker compose ps
docker compose logs -f cloud-panel
curl -I http://127.0.0.1:3130/healthz
```

## Intégration au dashboard

### Homepage

Ajouter une carte dans `homepage/services.yaml` :

```yaml
- Cloud Panel:
    icon: cloud-upload-outline
    href: https://dashboard.example.com/cloud-panel/
    description: Gestionnaire de fichiers
```

### Navigation

Ajouter un lien dans le header des autres panels si nécessaire.

## Variables d'environnement

### Globales (`.env`)

```
CLOUD_PANEL_PORT=3130
```

### Cloud Panel (`cloud-panel/.env`)

```
CLOUD_PANEL_MOUNT_PATH=/mnt/ultra-media
CLOUD_PANEL_RATE_LIMIT_CALLS=40
CLOUD_PANEL_RATE_LIMIT_SECONDS=60
CLOUD_PANEL_CSRF_TOKEN_TTL_SECONDS=43200
SENTRY_DSN=
SENTRY_ENVIRONMENT=production
```

## Règles absolues

1. **Jamais exposer** : URL internes, mots de passe, chemins absolus dans les logs ou le frontend
2. **Backend = seul point de contact** avec le système de fichiers
3. **CSRF obligatoire** sur toutes les mutations
4. **Rate limiting** sur toutes les mutations
5. **CSP stricte** : pas de inline, pas de CDN
6. **Pas de framework frontend** : HTML/CSS/JS natifs uniquement
7. **Tokens CSS partagés** : aucune couleur en dur
8. **Path traversal** : validation stricte avant toute opération
9. **Upload streaming** : pas de limite de taille, écriture atomique
10. **Erreurs structurées** : `{"code": str, "message": str, "recovery": str}`

## Checklist d'implémentation

- [ ] Créer la structure de dossiers `cloud-panel/`
- [ ] Écrire `Dockerfile`, `requirements.txt`, `.env.example`
- [ ] Implémenter `config.py`, `security.py`, `storage.py`
- [ ] Implémenter `routes/files.py` avec tous les endpoints
- [ ] Implémenter `main.py` avec middleware et configuration
- [ ] Créer `build.py` pour concaténer CSS/JS
- [ ] Développer `static/index.html` avec structure HTML
- [ ] Développer `static/app.js` avec toute la logique
- [ ] Développer `static/app.css` avec les styles
- [ ] Mettre à jour `docker-compose.yml` avec le service `cloud-panel`
- [ ] Mettre à jour `caddy/dashboard.conf` avec la route `/cloud-panel/*`
- [ ] Mettre à jour `.env.example` global avec `CLOUD_PANEL_PORT`
- [ ] Écrire les tests backend (`tests/test_backend.py`)
- [ ] Écrire les tests frontend (`tests/frontend_logic.test.js`)
- [ ] Builder les bundles CSS/JS
- [ ] Tester localement avec Docker
- [ ] Valider le responsive à toutes les tailles d'écran
- [ ] Vérifier la sécurité (CSRF, rate limiting, path traversal)
- [ ] Mettre à jour `AGENTS.md` avec la nouvelle architecture
- [ ] Mettre à jour `homepage/services.yaml` avec la carte Cloud Panel

## Notes finales

- Le cloud-panel réutilise la logique de `gofile-manager` mais avec FastAPI au lieu de Flask
- L'upload streaming permet de gérer des fichiers de toute taille sans limite mémoire
- La sécurité est critique : path traversal, CSRF, rate limiting doivent être implémentés correctement
- Le design doit être cohérent avec les autres panels (tokens CSS partagés)
- Les tests sont obligatoires avant de considérer la tâche terminée
- La validation visuelle responsive est obligatoire
