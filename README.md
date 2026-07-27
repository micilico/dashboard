# Dashboard

Dashboard personnel auto-hébergé pour piloter qBittorrent, Prowlarr, Jellyfin,
rclone et un espace de fichiers depuis une interface unique. Les services
applicatifs restent liés à `127.0.0.1` et sont publiés uniquement par Caddy,
derrière HTTPS et `basic_auth`.

## Architecture

```text
Internet
  └─ Caddy
      ├─ /                  → Homepage      :3001
      ├─ /torrent-panel/   → Torrent Panel :3110
      ├─ /prowlarr-panel/  → Prowlarr      :3120
      └─ /cloud-panel/     → Cloud Panel   :3130
                              │
            autossh ──────────┼─ qBittorrent :16141
                              └─ Prowlarr    :16124
            rclone FUSE ──────── /mnt/ultra-media
```

- `torrent-panel`, `prowlarr-panel` et `cloud-panel` utilisent FastAPI et un
  frontend HTML/CSS/JavaScript natif.
- `common/` contient les protections et primitives partagées : CSRF, limitation
  de débit, CSP, client API, gestion du focus et design system.
- Les conteneurs applicatifs sont non-root, en lecture seule, sans élévation de
  privilèges, avec un système de fichiers temporaire borné.
- Les secrets et URL internes restent exclusivement côté serveur.

## Démarrage

Prérequis : Docker Compose, Python 3.12 et Node.js 24 pour le développement.

```bash
cp .env.example .env
cp torrent-panel/.env.example torrent-panel/.env
cp prowlarr-panel/.env.example prowlarr-panel/.env
cp cloud-panel/.env.example cloud-panel/.env
```

Renseigner les fichiers `.env` sans les versionner, puis :

```bash
docker compose build torrent-panel prowlarr-panel cloud-panel
docker compose up -d
docker compose ps
```

Les services écoutent localement sur les ports `3001`, `3110`, `3120` et
`3130`. La configuration Caddy de référence se trouve dans
[`caddy/dashboard.conf`](caddy/dashboard.conf).

## Développement et qualité

```bash
make setup     # environnement Python et dépendances verrouillées
make build     # bundles CSS/JS et copie des polices auto-hébergées
make test      # tests backend et frontend
make audit     # audit des dépendances Python
make check     # build, compilation, tests et vérification des bundles
```

La CI exécute ces contrôles avec Python 3.12 et Node.js 24, puis construit les
trois images applicatives. Après toute modification frontend, les bundles
`static/dist/` doivent être reconstruits et versionnés.

## Vérification locale

```bash
curl -I http://127.0.0.1:3110/healthz
curl -I http://127.0.0.1:3120/healthz
curl -I http://127.0.0.1:3130/healthz
curl -I http://127.0.0.1:3110/readyz
curl -I http://127.0.0.1:3120/readyz
curl -I http://127.0.0.1:3130/readyz
```

`healthz` vérifie le processus ; `readyz` vérifie aussi les dépendances
nécessaires. Une readiness en erreur est normale tant que les tunnels et le
montage ne sont pas actifs.

## Infrastructure

1. Copier [`autossh/autossh-ultra.service`](autossh/autossh-ultra.service) dans
   `/etc/systemd/system/` et son fichier d’environnement hors du dépôt.
2. Adapter [`rclone/override.conf`](rclone/override.conf) à la commande réelle
   du serveur en conservant l’API RC sur `127.0.0.1:5572`.
3. Adapter le domaine et le hash `basic_auth` dans
   [`caddy/dashboard.conf`](caddy/dashboard.conf), valider puis recharger Caddy.
4. Ne jamais exposer directement les ports applicatifs, les tunnels SSH ou
   l’API rclone RC.

## Sauvegardes et exploitation

Créer une archive horodatée des volumes applicatifs :

```bash
docker compose --profile tools run --rm backup
```

La destination locale est contrôlée par `DASHBOARD_BACKUP_DIR` et ignorée par
Git. La procédure de restauration, de déploiement et de retour arrière est
documentée dans [`docs/OPERATIONS.md`](docs/OPERATIONS.md). Les critères de
validation fonctionnelle, responsive et accessible sont dans
[`docs/QUALITY.md`](docs/QUALITY.md).

## Sécurité

- Caddy est le seul point d’entrée public.
- Toute mutation exige un jeton CSRF et passe par une limite de débit.
- La CSP interdit les scripts inline, les CDN et l’encapsulation.
- Les erreurs sont structurées et nettoyées avant de parvenir au navigateur.
- Les données runtime (`*.db`, WAL, sauvegardes) ne sont jamais suivies par Git.
- Les liens de partage Cloud doivent être considérés comme des secrets
  temporaires ; utiliser un mot de passe et une expiration courte pour les
  contenus sensibles.

Inter Variable est auto-hébergée sous licence OFL ; la licence est conservée
dans [`common/fonts/LICENSE.txt`](common/fonts/LICENSE.txt).
