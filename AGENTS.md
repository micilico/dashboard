# AGENTS.md — Dashboard

## Vue d'ensemble

Dashboard personnel auto-hébergé agrégeant qBittorrent, Prowlarr, Jellyfin, rclone et un gestionnaire de fichiers cloud derrière un reverse proxy Caddy. Quatre services Docker + Homepage + infrastructure SSH/rclone sur un VPS ultra.cc, accessibles via un tunnel autossh.

## Architecture

```
Internet
  │
  ▼
Caddy (reverse proxy, basic_auth, HTTPS)
  │
  ├── /                    → Homepage        (127.0.0.1:3001, bridge Docker)
  ├── /cloud-panel/*       → cloud-panel     (127.0.0.1:3130, network_mode: host)
  ├── /torrent-panel/*     → torrent-panel   (127.0.0.1:3110, network_mode: host)
  ├── /prowlarr-panel/*    → prowlarr-panel  (127.0.0.1:3120, network_mode: host)
  ├── /activity/*          → torrent-panel
  ├── /storage-panel/*     → torrent-panel
  ├── /media-panel/*       → torrent-panel
  └── /health/*            → torrent-panel
```

### Flux réseau

- **autossh** : tunnel SSH persistant du VPS vers ultra.cc, exposant `127.0.0.1:16141` (qBittorrent) et `127.0.0.1:16124` (Prowlarr).
- **rclone** : montage FUSE `/mnt/ultra-media` avec `--rc` sur `127.0.0.1:5572`.
- Homepage (bridge Docker) accède aux services via `host.docker.internal`.
- torrent-panel et prowlarr-panel (host network) accèdent aux tunnels via `127.0.0.1`.

## Stack technique

| Composant | Technologie | Détails |
|---|---|---|
| Homepage | `ghcr.io/gethomepage/homepage:v1.13.1` | Image Docker officielle, config YAML |
| torrent-panel | Python 3.12, FastAPI, httpx, uvicorn | Backend + frontend HTML/CSS/JS natifs |
| prowlarr-panel | Python 3.12, FastAPI, httpx, uvicorn | Backend + frontend HTML/CSS/JS natifs |
| cloud-panel | Python 3.12, FastAPI, httpx, uvicorn | Gestionnaire de fichiers pour montage rclone |
| common | Package Python (`dashboard-common`) | Utilitaires partagés : CSRF, rate limiter, CSP, CSS tokens |
| Caddy | Reverse proxy | basic_auth, HSTS, CSP, compression zstd/gzip |
| autossh | systemd | Tunnel SSH persistant |
| rclone | FUSE + rc | Montage distant avec API de contrôle locale |
| Monitoring | Sentry SDK (optionnel) | DSN via variable d'env |

### Dépendances Python (identiques pour les deux panels)

```
fastapi==0.139.2
httpx==0.28.1
starlette==1.3.1
uvicorn[standard]==0.35.0
sentry-sdk[fastapi]==2.42.0
```

## Structure des fichiers

```
dashboard/
├── docker-compose.yml          # Orchestration des 3 conteneurs
├── .env.example                # Variables globales
├── caddy/
│   └── dashboard.conf          # Configuration du reverse proxy
├── homepage/                   # Configuration Homepage (YAML)
│   ├── settings.yaml           # Thème sombre, langue FR
│   ├── services.yaml           # Cartes qBittorrent, Prowlarr, Jellyfin, rclone
│   ├── widgets.yaml            # Horloge, recherche, ressources disque
│   ├── bookmarks.yaml          # Vide par défaut
│   ├── custom.css / custom.js  # Personnalisations
│   ├── docker.yaml / kubernetes.yaml / proxmox.yaml
├── common/                     # Package partagé dashboard-common
│   ├── __init__.py             # Exports + resolve_css_imports()
│   ├── csrf.py                 # Protection CSRF (cookie + header + TTL)
│   ├── rate_limiter.py         # Limiteur de débit par clé
│   ├── security.py             # CSP stricte, error_detail()
│   ├── monitoring.py           # init_sentry()
│   ├── types.py                # ErrorDetail TypedDict
│   ├── css/                    # Design system partagé
│   │   ├── index.css           # Point d'entrée CSS (imports)
│   │   ├── tokens.css          # Variables CSS sémantiques
│   │   ├── base.css            # Reset et styles de base
│   │   ├── fonts.css           # Inter Variable
│   │   ├── utilities.css       # Classes utilitaires
│   │   └── components/         # Boutons, cartes, dialogues, formulaires, navigation, tableaux
│   └── js/
│       ├── api.js              # Client API partagé
│       └── focus-trap.js       # Piégeage focus pour dialogues
├── torrent-panel/
│   ├── Dockerfile              # Python 3.12-slim, user nobody
│   ├── build.py                # Concatène CSS/JS → static/dist/
│   ├── requirements.txt
│   ├── .env.example
│   ├── torrent_panel/
│   │   ├── main.py             # FastAPI app, middleware, routes statiques
│   │   ├── config.py           # Configuration via env
│   │   ├── models.py           # Modèles Pydantic
│   │   ├── qbittorrent.py      # Client qBittorrent
│   │   ├── preview.html
│   │   ├── routes/             # Endpoints API
│   │   │   ├── torrents.py     # CRUD torrents
│   │   │   ├── dashboard.py    # Vue d'ensemble, santé, stockage, activité
│   │   │   ├── media_automation.py
│   │   │   ├── notifications.py
│   │   │   └── automations.py  # Règles d'automatisation
│   │   ├── services/           # Logique métier
│   │   │   ├── media_automation.py  # Gestion transfert → Jellyfin → rclone
│   │   │   ├── monitoring.py   # Checks santé, agrégation dashboard
│   │   │   ├── notifications.py
│   │   │   └── automations.py
│   │   └── static/             # Frontend
│   │       ├── index.html      # App principale (vues : home, torrents, prowlarr)
│   │       ├── app.js          # Logique principale
│   │       ├── app.css
│   │       ├── console.js      # Pages secondaires (activity, storage, media, health)
│   │       ├── console.css
│   │       ├── activity.html / storage.html / media.html / health.html
│   │       ├── css/            # Modules CSS (home.css, responsive.css, etc.)
│   │       └── dist/           # Bundles générés par build.py
│   └── tests/
│       ├── test_backend.py     # pytest
│       └── frontend_logic.test.js
├── cloud-panel/
│   ├── Dockerfile
│   ├── build.py
│   ├── requirements.txt
│   ├── .env.example
│   ├── cloud_panel/
│   │   ├── main.py             # FastAPI app, middleware, routes statiques
│   │   ├── config.py           # Configuration via env
│   │   ├── security.py         # Path traversal protection
│   │   ├── storage.py          # Logique fichiers (scandir, upload, download, opérations)
│   │   ├── routes/
│   │   │   ├── files.py        # Endpoints API (list, upload, download, mkdir, rename, delete)
│   │   └── static/
│   │       ├── index.html      # App principale
│   │       ├── app.js
│   │       ├── app.css
│   │       ├── css/
│   │       └── dist/
│   └── tests/
│       ├── test_backend.py
│       └── frontend_logic.test.js
├── prowlarr-panel/
├── autossh/
│   ├── autossh-ultra.service   # Unité systemd
│   └── dashboard-autossh.example
├── rclone/
│   └── override.conf           # Override systemd pour --rc
└── docs/
    └── mockups/                # Maquettes de référence
```

## Commandes

### Build

```bash
# Bundles CSS/JS (à exécuter après toute modification frontend)
python torrent-panel/build.py
python prowlarr-panel/build.py
python cloud-panel/build.py

# Reconstruction Docker
docker compose build torrent-panel prowlarr-panel cloud-panel
docker compose up -d
```

### Tests

```bash
# Backend Python (pytest)
pytest torrent-panel/tests/test_backend.py
pytest prowlarr-panel/tests/test_backend.py
pytest cloud-panel/tests/test_backend.py

# Frontend JS (node)
node torrent-panel/tests/frontend_logic.test.js
node prowlarr-panel/tests/frontend_logic.test.js
node cloud-panel/tests/frontend_logic.test.js
```

### Vérification

```bash
docker compose ps
docker compose logs -f torrent-panel prowlarr-panel cloud-panel
curl -I http://127.0.0.1:3110/healthz
curl -I http://127.0.0.1:3120/healthz
curl -I http://127.0.0.1:3130/healthz
```

## Sécurité — règles absolues

1. **Jamais exposer côté frontend ou dans les logs** : URL internes, mots de passe, cookies de session qBittorrent, clés API, passkeys, identifiants de connexion.
2. **Backend = seul point de contact** avec les services distants. Le frontend ne reçoit que des données sanitized.
3. **CSRF obligatoire** sur toutes les mutations (cookie httponly + header + TTL).
4. **Rate limiting** par type d'action (search, test, modify, grab pour Prowlarr ; global pour Torrents).
5. **CSP stricte** : `default-src 'self'`, pas de inline, pas de CDN, pas de frame.
6. **Headers** : HSTS, X-Content-Type-Options, Referrer-Policy, Permissions-Policy sur chaque réponse.
7. **basic_auth Caddy** : seule authentification exposée publiquement.
8. **Pas de framework frontend** : HTML, CSS et JavaScript natifs uniquement. Ne pas introduire React, Vue, Tailwind ou similaire.
9. **Pas de dépendance externe au runtime** : tout est self-hosted, y compris les polices (Inter Variable).

## Conventions de code

### Backend Python

- Python 3.12+, type hints partout, `from __future__ import annotations`.
- FastAPI sans docs OpenAPI (`docs_url=None, redoc_url=None, openapi_url=None`).
- Configuration exclusivement par variables d'environnement (`os.getenv`).
- Erreurs structurées : `{"code": str, "message": str, "recovery": str}`.
- Logging via `logging.getLogger(__name__)`, niveau par env.
- Docker : `USER nobody`, pas de root.

### Frontend

- HTML/CSS/JS natifs, pas de transpilation ni de bundler lourd.
- Build par concaténation via `build.py` (CSS : `resolve_css_imports` ; JS : jointure de fichiers).
- Bundles : `static/dist/app.min.css`, `static/dist/app.min.js`, `static/dist/console.min.js`.
- Cachebuster : `?v=BUILD` sur les ressources statiques.
- Configuration frontend via `window.__TORRENT_PANEL_CONFIG__` / `window.__PROWLARR_PANEL_CONFIG__` / `window.__DASHBOARD_CONSOLE_CONFIG__` servis par `/config.js`.
- Préfixes publics configurables (ex: `/torrent-panel`, `/prowlarr-panel`) pour tous les liens et montages statiques.

### CSS / Design system

- Tokens sémantiques dans `common/css/tokens.css` — aucune couleur en dur dans les composants.
- Palette : fond `#07080B`, surfaces `#101217`/`#151821`, accent indigo `#7C6CFF`, succès `#5EE6A8`, danger `#FF6B72`.
- Police : Inter Variable, graisses 400/500/600/700, `font-variant-numeric: tabular-nums` pour les données.
- Rayons : 12 px (contrôles), 18 px (panneaux), 24 px (cartes), 32 px (cartes héro).
- Espacement : échelle 4/8/12/16/24/32/48/64/96 px.
- Breakpoints : 375, 768, 1024, 1440, 1600 px.
- Style : minimalisme sombre premium, pas de néon, pas de glassmorphism, pas d'emojis comme icônes.

### Accessibilité

- Navigation clavier complète, focus visible 2-3 px.
- `aria-current="page"` uniquement sur l'entrée active.
- Icônes SVG avec `aria-label` ou `aria-hidden="true"` si décoratives.
- Contraste WCAG AA : 4.5:1 texte normal, 3:1 grand texte.
- `prefers-reduced-motion` respecté.
- Cibles interactives ≥ 44×44 px.
- Hiérarchie de titres séquentielle, un seul `h1` par page.

## APIs

### Torrent Panel (`/api/`)

| Méthode | Endpoint | Description |
|---|---|---|
| GET | `/api/torrents` | Liste des torrents |
| POST | `/api/torrents/add` | Ajouter un torrent/magnet |
| POST | `/api/torrents/pause` | Mettre en pause |
| POST | `/api/torrents/resume` | Reprendre |
| POST | `/api/torrents/delete` | Supprimer |
| GET | `/api/dashboard` | Snapshot vue d'ensemble |
| GET | `/api/health` | État de santé complet |
| GET | `/api/activity` | Activité récente |
| GET | `/api/storage` | Statistiques stockage |
| GET | `/healthz` | Liveness |
| GET | `/readyz` | Readiness (vérifie qBittorrent) |

### Prowlarr Panel (`/api/`)

| Méthode | Endpoint | Description |
|---|---|---|
| GET | `/api/overview` | Résumé connexion Prowlarr |
| GET | `/api/indexers` | Liste des indexers |
| POST | `/api/indexers/test` | Tester un indexer |
| POST | `/api/indexers/test-all` | Tester tous les indexers |
| POST | `/api/indexers/enabled` | Activer/désactiver un indexer |
| POST | `/api/search` | Rechercher des releases |
| POST | `/api/grab` | Récupérer une release via Prowlarr |
| GET | `/api/applications` | Applications connectées |
| GET | `/api/health` | Alertes système |
| GET | `/api/history` | Historique récent |
| GET | `/api/capabilities` | Capacités détectées |
| POST | `/api/discover` | Redétection des capacités |
| GET | `/api/session` | Token CSRF |
| GET | `/healthz` | Liveness |
| GET | `/readyz` | Readiness (vérifie Prowlarr) |

### Cloud Panel (`/api/`)

| Méthode | Endpoint | Description |
|---|---|---|
| GET | `/api/session` | Token CSRF |
| GET | `/api/files` | Liste des fichiers (query: path) |
| POST | `/api/files/upload` | Upload fichier (streaming chunk-by-chunk) |
| GET | `/api/files/download` | Téléchargement fichier (query: path) |
| POST | `/api/files/mkdir` | Créer un dossier |
| POST | `/api/files/rename` | Renommer fichier/dossier |
| POST | `/api/files/delete` | Supprimer fichier/dossier |
| POST | `/api/files/refresh` | Invalider le cache |
| GET | `/healthz` | Liveness |
| GET | `/readyz` | Readiness |

## Variables d'environnement clés

### Globales (`.env`)

| Variable | Rôle |
|---|---|
| `HOMEPAGE_ALLOWED_HOSTS` | Domaines autorisés Homepage |
| `HOMEPAGE_VAR_QBITTORRENT_USERNAME/PASSWORD` | Widget Homepage |
| `HOMEPAGE_VAR_PROWLARR_API_KEY` | Widget Homepage |
| `HOMEPAGE_VAR_JELLYFIN_URL/API_KEY` | Widget Homepage |
| `TORRENT_PANEL_PORT` | Port écoute (défaut 3110) |
| `PROWLARR_PANEL_PORT` | Port écoute (défaut 3120) |
| `CLOUD_PANEL_PORT` | Port écoute (défaut 3130) |
| `SENTRY_DSN` | Monitoring (optionnel) |

### Torrent Panel (`torrent-panel/.env`)

| Variable | Rôle |
|---|---|
| `QBITTORRENT_URL` | URL qBittorrent (tunnel) |
| `QBITTORRENT_USERNAME/PASSWORD` | Authentification backend uniquement |
| `TORRENT_PANEL_MEDIA_AUTOMATION_ENABLED` | Automatisation médias |
| `TORRENT_PANEL_RCLONE_REFRESH_MODE` | `auto` = rclone rc puis fallback systemd |
| `TORRENT_PANEL_JELLYFIN_LIBRARY_MAP` | Mapping catégories → IDs Jellyfin |
| `TORRENT_PANEL_JELLYFIN_API_URL/API_KEY` | Backend uniquement |

### Cloud Panel (`cloud-panel/.env`)

| Variable | Rôle |
|---|---|
| `CLOUD_PANEL_MOUNT_PATH` | Chemin du montage rclone (défaut `/mnt/ultra-media`) |
| `CLOUD_PANEL_RATE_LIMIT_CALLS` | Limite d'appels (défaut 40) |
| `CLOUD_PANEL_RATE_LIMIT_SECONDS` | Fenêtre de temps (défaut 60) |
| `CLOUD_PANEL_CSRF_TOKEN_TTL_SECONDS` | Durée de vie du token CSRF (défaut 43200) |

### Prowlarr Panel (`prowlarr-panel/.env`)

| Variable | Rôle |
|---|---|
| `PROWLARR_URL` | URL Prowlarr (tunnel) |
| `PROWLARR_API_KEY` | Authentification backend uniquement |
| `PROWLARR_PANEL_RATE_LIMIT` | Limites par action : `search=20/60,test=20/60,...` |

## Design — références

- `DESIGN_INSTRUCTIONS.md` : cahier des charges complet du design premium.
- `MOCKUP_IMPLEMENTATION_INSTRUCTIONS.md` : instructions d'implémentation du mockup overview.
- `OVERVIEW_REDESIGN_IMPLEMENTATION_INSTRUCTIONS.md` : refonte visuelle complète (remplace les précédents pour la vue d'ensemble — pas de carte stockage, grille 3 métriques + Services 8/12 + Activité 4/12).
- `AI_FIX_INSTRUCTIONS.md` : corrections navigation, recherche torrents, santé SSH.
- Mockup de référence : `docs/mockups/overview-without-media-space.png`.

## Règles de modification

1. **Lire les fichiers concernés avant toute modification.**
2. **Ne jamais supprimer de fonctionnalité existante.**
3. **Ne jamais coder de secret, d'URL interne ou de donnée en dur.**
4. **Reconstruire les bundles après chaque changement frontend** (`build.py`).
5. **Exécuter les tests** (backend + frontend) avant de considérer une tâche terminée.
6. **Préserver les préfixes publics configurables** dans tous les liens.
7. **Les tokens CSS partagés sont la source de vérité** — pas de couleurs en dur dans les composants.
8. **Validation visuelle obligatoire** : comparer le rendu navigateur aux mockups à 1600, 1440, 1024, 768 et 375 px.
9. **Pas de framework frontend** — HTML/CSS/JS natifs uniquement.
10. **Les erreurs API ne doivent jamais contenir de secret** — utiliser `error_detail(code, message, recovery)`.
