# Dashboard

Stack retenue :
- Homepage en Docker avec `network_mode: host`
- `autossh` en service systemd pour joindre qBittorrent et Prowlarr sur ultra.cc
- `rclone` avec `--rc` actif sur `127.0.0.1:5572`
- `Caddy` en reverse proxy avec `basic_auth`

## Arborescence

```text
.
├── autossh/
├── caddy/
├── homepage/
├── rclone/
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
HOMEPAGE_VAR_PROWLARR_API_KEY=change-me
HOMEPAGE_VAR_JELLYFIN_API_KEY=change-me
```

## 2. Installer Homepage

Depuis le dossier du projet :

```bash
docker compose up -d
docker compose logs -f homepage
```

Homepage ecoute sur `127.0.0.1:3000` via le reseau host.

## 3. Installer le tunnel autossh

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

## 4. Activer rclone rc

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

## 5. Configurer Caddy

Mettre a jour [caddy/dashboard.conf](/Users/corentinkern/Documents/Dashboard/caddy/dashboard.conf:1) :
- remplacer `dashboard.example.com`
- generer un hash de mot de passe
- remplacer `admin` si besoin

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
curl -I http://127.0.0.1:3000
curl -I https://dashboard.example.com
```

## 6. Verifications finales

Verifier les points suivants :
- `docker compose ps` montre Homepage `Up`
- `autossh-ultra.service` est actif
- `rclone` repond sur `127.0.0.1:5572`
- `https://dashboard.example.com` demande bien l'authentification HTTP
- les widgets Homepage remontent les donnees de qBittorrent, Prowlarr, Jellyfin et rclone

## Notes

- [homepage/services.yaml](/Users/corentinkern/Documents/Dashboard/homepage/services.yaml:1) utilise uniquement des endpoints locaux du VPS
- aucun lien public vers qBittorrent ou Prowlarr n'est expose pour l'instant
- [homepage/bookmarks.yaml](/Users/corentinkern/Documents/Dashboard/homepage/bookmarks.yaml:1) est vide par choix et peut etre complete plus tard
