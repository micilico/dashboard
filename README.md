# Dashboard

Stack retenue :
- Homepage en Docker expose uniquement sur `127.0.0.1:3001`
- `torrent-panel` en Docker expose uniquement sur `127.0.0.1:3110`
- `prowlarr-panel` en Docker expose uniquement sur `127.0.0.1:3120`
- les conteneurs joignent les services du host via `host.docker.internal`
- `autossh` en service systemd pour joindre qBittorrent et Prowlarr sur ultra.cc
- `rclone` avec `--rc` actif sur `127.0.0.1:5572`
- `Caddy` en reverse proxy avec `basic_auth`, Homepage sur `/`, Torrent Panel sur `/torrent-panel/` et Prowlarr Panel sur `/prowlarr-panel/`

## Arborescence

```text
.
├── autossh/
├── caddy/
├── homepage/
├── prowlarr-panel/
├── rclone/
├── torrent-panel/
├── .env.example
└── docker-compose.yml
```

## 1. Preparer les variables Homepage

Creer le fichier `.env` a partir de `.env.example`, puis remplir les vraies valeurs :

```env
PUID=1000
PGID=1000
TZ=Europe/Paris
HOMEPAGE_ALLOWED_HOSTS=dashboard.example.com
HOMEPAGE_VAR_QBITTORRENT_USERNAME=change-me
HOMEPAGE_VAR_QBITTORRENT_PASSWORD=change-me
TORRENT_PANEL_PORT=3110
PROWLARR_PANEL_PORT=3120
HOMEPAGE_VAR_PROWLARR_API_KEY=change-me
HOMEPAGE_VAR_JELLYFIN_API_KEY=change-me
```

Creer aussi `torrent-panel/.env` a partir de [torrent-panel/.env.example](/Users/corentinkern/Documents/Dashboard/torrent-panel/.env.example:1) :

```env
QBITTORRENT_URL=http://host.docker.internal:16141
QBITTORRENT_USERNAME=change-me
QBITTORRENT_PASSWORD=change-me
QBITTORRENT_TIMEOUT_SECONDS=8
TORRENT_PANEL_MEDIA_AUTOMATION_ENABLED=true
TORRENT_PANEL_MEDIA_AUTOMATION_DEBOUNCE_SECONDS=45
TORRENT_PANEL_MEDIA_MOUNT_PATH=/mnt/ultra-media
TORRENT_PANEL_RCLONE_REFRESH_MODE=auto
TORRENT_PANEL_RCLONE_RC_REFRESH_URL=http://host.docker.internal:5572/vfs/refresh
TORRENT_PANEL_RCLONE_SYSTEMD_RESTART_CMD=
TORRENT_PANEL_JELLYFIN_API_URL=http://host.docker.internal:8096
TORRENT_PANEL_JELLYFIN_API_KEY=change-me
TORRENT_PANEL_JELLYFIN_LIBRARY_MAP=films=<jellyfin-id>,series=<jellyfin-id>,musique=<jellyfin-id>
TORRENT_PANEL_JELLYFIN_GLOBAL_FALLBACK=true
```

Les variables `QBITTORRENT_USERNAME` et `QBITTORRENT_PASSWORD` sont lues uniquement par le backend Torrent Panel. Elles peuvent avoir les memes valeurs que les variables `HOMEPAGE_VAR_QBITTORRENT_*`, mais aucun secret ne doit etre ajoute aux fichiers versionnes.

Pour l'automatisation medias :
- `TORRENT_PANEL_RCLONE_REFRESH_MODE=auto` tente d'abord `rclone rc vfs/refresh`, puis une commande systemd fixe si `TORRENT_PANEL_RCLONE_SYSTEMD_RESTART_CMD` est definie
- verifier sur le serveur reel le nom exact de l'unite systemd avant de remplir une commande de secours
- `TORRENT_PANEL_JELLYFIN_LIBRARY_MAP` mappe les categories qBittorrent vers des identifiants Jellyfin fournis uniquement au backend
- si la categorie n'est pas mappee, `TORRENT_PANEL_JELLYFIN_GLOBAL_FALLBACK=true` autorise un scan global

Creer aussi `prowlarr-panel/.env` a partir de [prowlarr-panel/.env.example](/Users/corentinkern/Documents/Dashboard/prowlarr-panel/.env.example:1) :

```env
PROWLARR_URL=http://host.docker.internal:16124/prowlarr
PROWLARR_API_KEY=change-me
PROWLARR_TIMEOUT_SECONDS=8
PROWLARR_RELEASE_CACHE_TTL_SECONDS=900
PROWLARR_PANEL_HOST=0.0.0.0
PROWLARR_PANEL_PORT=3120
PROWLARR_PANEL_LOG_LEVEL=INFO
PROWLARR_PANEL_RATE_LIMIT=search=20/60,test=20/60,modify=10/60,grab=10/60
```

`PROWLARR_API_KEY` peut avoir la meme valeur que `HOMEPAGE_VAR_PROWLARR_API_KEY`, mais elle reste fournie uniquement au conteneur `prowlarr-panel`. Le frontend ne recoit jamais la cle, l'URL interne Prowlarr, les passkeys ou les champs sensibles des indexers.

## 2. Installer Homepage

Depuis le dossier du projet :

```bash
docker compose up -d
docker compose logs -f homepage torrent-panel prowlarr-panel
```

Homepage est publie sur `127.0.0.1:3001`. Torrent Panel est publie sur `127.0.0.1:3110` par defaut. Prowlarr Panel est publie sur `127.0.0.1:3120` par defaut. Aucun de ces ports n'est ouvert sur une interface publique.

## 3. Utiliser Torrent Panel

Torrent Panel est accessible depuis la carte qBittorrent dans Homepage ou directement via :

```text
https://dashboard.example.com/torrent-panel/
```

Le navigateur appelle uniquement l'API limitee du backend Torrent Panel :

- `GET /api/torrents`
- `POST /api/torrents/add`
- `POST /api/torrents/pause`
- `POST /api/torrents/resume`
- `POST /api/torrents/delete`

Le backend gere la connexion qBittorrent, conserve le cookie de session cote serveur, renouvelle la session apres expiration et ne renvoie jamais l'URL interne, le mot de passe ou le cookie qBittorrent au frontend.

## 4. Utiliser Prowlarr Panel

Prowlarr Panel est accessible depuis la carte Prowlarr dans Homepage ou directement via :

```text
https://dashboard.example.com/prowlarr-panel/
```

Flux :

```text
Navigateur -> Caddy basic_auth -> Prowlarr Panel -> API Prowlarr via tunnel SSH
```

Le navigateur appelle uniquement l'API limitee du backend Prowlarr Panel :

- `GET /api/overview`
- `GET /api/indexers`
- `POST /api/indexers/test`
- `POST /api/indexers/test-all`
- `POST /api/indexers/enabled`
- `POST /api/search`
- `POST /api/grab`
- `GET /api/applications`
- `GET /api/health`
- `GET /api/history`
- `GET /api/capabilities`
- `POST /api/discover`

Endpoints Prowlarr utilises apres decouverte runtime :

- `GET /api/v1/system/status` pour version, base path et readiness
- `GET /api/v1/indexer` et `GET /api/v1/indexer/{id}` pour les indexers
- `POST /api/v1/indexer/test` pour tester un ou tous les indexers
- `PUT /api/v1/indexer/{id}` pour activer/desactiver lorsque l'API de l'instance l'accepte
- `POST /api/v1/search` puis fallback `GET /api/v1/search` pour rechercher des releases
- `POST /api/v1/search` avec la release complete cachee cote serveur pour le grab natif vers le download client configure dans Prowlarr
- `GET /api/v1/applications` puis fallback `GET /api/v1/application` pour les applications
- `GET /api/v1/health` pour les alertes systeme
- `GET /api/v1/history` pour l'historique recent

Compatibilite et limites :
- au demarrage, le service interroge l'instance reelle et expose les capacites detectees dans `/api/capabilities`
- si le tunnel SSH est indisponible, la detection reste en attente et l'UI affiche une erreur explicite
- les endpoints non disponibles retournent une erreur structuree sans secret
- les reglages avances restent dans l'interface Prowlarr native : creation complete d'indexer, saisie de passkey, proxies, edition avancee des applications, mises a jour et sauvegardes
- l'envoi vers qBittorrent utilise le grab natif Prowlarr; aucun lien de telechargement prive n'est renvoye au navigateur

Note de detection initiale : depuis cet environnement, `curl http://127.0.0.1:16124/prowlarr/...` echoue avec `Failed to connect`, donc la version reelle n'a pas pu etre detectee hors runtime. Une fois le tunnel actif sur le VPS, verifier :

```bash
curl -I http://127.0.0.1:3120/readyz
docker compose logs prowlarr-panel
```

## 5. Installer le tunnel autossh

Copier [autossh/autossh-ultra.service](/Users/corentinkern/Documents/Dashboard/autossh/autossh-ultra.service:1) sur le VPS dans :

```text
/etc/systemd/system/autossh-ultra.service
```

Puis activer le service :

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now autossh-ultra.service
sudo systemctl status autossh-ultra.service
ss -ltnp | grep 16141
ss -ltnp | grep 16124
```

Le tunnel doit exposer localement :
- `127.0.0.1:16141` vers qBittorrent ultra.cc
- `127.0.0.1:16124` vers Prowlarr ultra.cc

Comme Homepage et Torrent Panel tournent en bridge Docker, ils appellent ces services avec `host.docker.internal`. Verifier sur le VPS que les ports bindes sur le host sont bien joignables depuis les conteneurs ; sinon il faudra adapter le bind du tunnel ou ajouter un relais local sans exposition publique.

## 6. Activer rclone rc

Avant d'appliquer l'override, recuperer la commande exacte du service existant :

```bash
systemctl cat rclone
systemctl show -p ExecStart rclone
```

Modifier ensuite [rclone/override.conf](/Users/corentinkern/Documents/Dashboard/rclone/override.conf:1) :
- remplacer la ligne `ExecStart=` complete par la vraie commande actuelle
- conserver a la fin : `--rc --rc-addr 127.0.0.1:5572 --rc-no-auth`

Installer l'override sur le VPS :

```bash
sudo mkdir -p /etc/systemd/system/rclone.service.d
sudo cp rclone/override.conf /etc/systemd/system/rclone.service.d/override.conf
sudo systemctl daemon-reload
sudo systemctl restart rclone
sudo systemctl status rclone
curl -X POST http://127.0.0.1:5572/core/stats
```

`rclone rc` ne doit jamais etre expose publiquement. Il reste bind sur `127.0.0.1`.

## 7. Configurer Caddy

Mettre a jour [caddy/dashboard.conf](/Users/corentinkern/Documents/Dashboard/caddy/dashboard.conf:1) :
- remplacer `dashboard.example.com`
- generer un hash de mot de passe
- remplacer `admin` si besoin
- verifier que le port `127.0.0.1:3110` correspond a `TORRENT_PANEL_PORT`
- verifier que le port `127.0.0.1:3120` correspond a `PROWLARR_PANEL_PORT`

Commande pour generer le hash :

```bash
caddy hash-password --plaintext 'mot-de-passe'
```

Exemple d'installation si Caddy charge les vhosts depuis `/etc/caddy/conf.d/` :

```bash
sudo cp caddy/dashboard.conf /etc/caddy/conf.d/dashboard.conf
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Verifier ensuite :

```bash
curl -I http://127.0.0.1:3001
curl -I http://127.0.0.1:3110/healthz
curl -I http://127.0.0.1:3120/healthz
curl -I https://dashboard.example.com
curl -I https://dashboard.example.com/torrent-panel/
curl -I https://dashboard.example.com/prowlarr-panel/
```

## 8. Verifications finales

Verifier les points suivants :
- `docker compose ps` montre Homepage, Torrent Panel et Prowlarr Panel `Up`
- `autossh-ultra.service` est actif
- `rclone` repond sur `127.0.0.1:5572`
- `https://dashboard.example.com` demande bien l'authentification HTTP
- `https://dashboard.example.com/torrent-panel/` charge l'interface apres authentification HTTP
- `https://dashboard.example.com/prowlarr-panel/` charge l'interface apres authentification HTTP
- les widgets Homepage remontent les donnees de qBittorrent, Prowlarr, Jellyfin et rclone
- Torrent Panel liste les torrents, ajoute un magnet, met en pause, reprend et supprime dans les deux modes
- Prowlarr Panel liste les indexers, teste un indexer, recherche une release et envoie une release via Prowlarr
- les logs `docker compose logs torrent-panel` ne contiennent ni mot de passe ni cookie qBittorrent
- les logs `docker compose logs prowlarr-panel` ne contiennent ni cle API, ni cookie, ni passkey, ni URL privee

Commandes utiles :

```bash
docker compose config
docker compose build torrent-panel prowlarr-panel
docker compose up -d
docker compose logs -f torrent-panel prowlarr-panel
```

## Retour arriere

Pour retirer Torrent Panel sans toucher a Homepage :

```bash
docker compose stop torrent-panel
docker compose rm torrent-panel
```

Puis supprimer le bloc `torrent-panel` de [docker-compose.yml](/Users/corentinkern/Documents/Dashboard/docker-compose.yml:1), le bloc `/torrent-panel/` de [caddy/dashboard.conf](/Users/corentinkern/Documents/Dashboard/caddy/dashboard.conf:1), et le `href` de la carte qBittorrent dans [homepage/services.yaml](/Users/corentinkern/Documents/Dashboard/homepage/services.yaml:1).

Pour retirer Prowlarr Panel sans toucher a Homepage :

```bash
docker compose stop prowlarr-panel
docker compose rm prowlarr-panel
```

Puis supprimer le bloc `prowlarr-panel` de [docker-compose.yml](/Users/corentinkern/Documents/Dashboard/docker-compose.yml:1), le bloc `/prowlarr-panel/` de [caddy/dashboard.conf](/Users/corentinkern/Documents/Dashboard/caddy/dashboard.conf:1), et le `href` de la carte Prowlarr dans [homepage/services.yaml](/Users/corentinkern/Documents/Dashboard/homepage/services.yaml:1).

## Notes

- [homepage/services.yaml](/Users/corentinkern/Documents/Dashboard/homepage/services.yaml:1) utilise uniquement des endpoints locaux du VPS
- le lien Homepage pointe vers Torrent Panel, pas vers qBittorrent
- aucun lien public vers qBittorrent ou Prowlarr n'est expose
- [homepage/bookmarks.yaml](/Users/corentinkern/Documents/Dashboard/homepage/bookmarks.yaml:1) est vide par choix et peut etre complete plus tard
- Torrent Panel applique un jeton CSRF pour les actions, une limite simple d'actions par minute, des timeouts reseau et des messages d'erreur sans secrets
- Prowlarr Panel applique un jeton CSRF pour les actions, des limites separees pour recherches/tests/modifications/grabs, des timeouts reseau et des messages d'erreur sans secrets
