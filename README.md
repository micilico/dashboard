# Dashboard

Stack retenue :
- Homepage en Docker expose uniquement sur `127.0.0.1:3001`
- `torrent-panel` en Docker expose uniquement sur `127.0.0.1:3110`
- les conteneurs joignent les services du host via `host.docker.internal`
- `autossh` en service systemd pour joindre qBittorrent et Prowlarr sur ultra.cc
- `rclone` avec `--rc` actif sur `127.0.0.1:5572`
- `Caddy` en reverse proxy avec `basic_auth`, Homepage sur `/` et Torrent Panel sur `/torrent-panel/`

## Arborescence

```text
.
├── autossh/
├── caddy/
├── homepage/
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
HOMEPAGE_VAR_PROWLARR_API_KEY=change-me
HOMEPAGE_VAR_JELLYFIN_API_KEY=change-me
```

Creer aussi `torrent-panel/.env` a partir de [torrent-panel/.env.example](/Users/corentinkern/Documents/Dashboard/torrent-panel/.env.example:1) :

```env
QBITTORRENT_URL=http://host.docker.internal:16141
QBITTORRENT_USERNAME=change-me
QBITTORRENT_PASSWORD=change-me
QBITTORRENT_TIMEOUT_SECONDS=8
```

Les variables `QBITTORRENT_USERNAME` et `QBITTORRENT_PASSWORD` sont lues uniquement par le backend Torrent Panel. Elles peuvent avoir les memes valeurs que les variables `HOMEPAGE_VAR_QBITTORRENT_*`, mais aucun secret ne doit etre ajoute aux fichiers versionnes.

## 2. Installer Homepage

Depuis le dossier du projet :

```bash
docker compose up -d
docker compose logs -f homepage torrent-panel
```

Homepage est publie sur `127.0.0.1:3001`. Torrent Panel est publie sur `127.0.0.1:3110` par defaut. Aucun des deux ports n'est ouvert sur une interface publique.

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

## 4. Installer le tunnel autossh

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

## 5. Activer rclone rc

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

## 6. Configurer Caddy

Mettre a jour [caddy/dashboard.conf](/Users/corentinkern/Documents/Dashboard/caddy/dashboard.conf:1) :
- remplacer `dashboard.example.com`
- generer un hash de mot de passe
- remplacer `admin` si besoin
- verifier que le port `127.0.0.1:3110` correspond a `TORRENT_PANEL_PORT`

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
curl -I https://dashboard.example.com
curl -I https://dashboard.example.com/torrent-panel/
```

## 7. Verifications finales

Verifier les points suivants :
- `docker compose ps` montre Homepage et Torrent Panel `Up`
- `autossh-ultra.service` est actif
- `rclone` repond sur `127.0.0.1:5572`
- `https://dashboard.example.com` demande bien l'authentification HTTP
- `https://dashboard.example.com/torrent-panel/` charge l'interface apres authentification HTTP
- les widgets Homepage remontent les donnees de qBittorrent, Prowlarr, Jellyfin et rclone
- Torrent Panel liste les torrents, ajoute un magnet, met en pause, reprend et supprime dans les deux modes
- les logs `docker compose logs torrent-panel` ne contiennent ni mot de passe ni cookie qBittorrent

Commandes utiles :

```bash
docker compose config
docker compose build torrent-panel
docker compose up -d
docker compose logs -f torrent-panel
```

## Retour arriere

Pour retirer Torrent Panel sans toucher a Homepage :

```bash
docker compose stop torrent-panel
docker compose rm torrent-panel
```

Puis supprimer le bloc `torrent-panel` de [docker-compose.yml](/Users/corentinkern/Documents/Dashboard/docker-compose.yml:1), le bloc `/torrent-panel/` de [caddy/dashboard.conf](/Users/corentinkern/Documents/Dashboard/caddy/dashboard.conf:1), et le `href` de la carte qBittorrent dans [homepage/services.yaml](/Users/corentinkern/Documents/Dashboard/homepage/services.yaml:1).

## Notes

- [homepage/services.yaml](/Users/corentinkern/Documents/Dashboard/homepage/services.yaml:1) utilise uniquement des endpoints locaux du VPS
- le lien Homepage pointe vers Torrent Panel, pas vers qBittorrent
- aucun lien public vers qBittorrent ou Prowlarr n'est expose
- [homepage/bookmarks.yaml](/Users/corentinkern/Documents/Dashboard/homepage/bookmarks.yaml:1) est vide par choix et peut etre complete plus tard
- Torrent Panel applique un jeton CSRF pour les actions, une limite simple d'actions par minute, des timeouts reseau et des messages d'erreur sans secrets
