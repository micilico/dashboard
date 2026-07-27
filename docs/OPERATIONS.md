# Exploitation

## Sauvegarde

Le service ponctuel `backup` archive les volumes `torrent-panel-data` et
`cloud-panel-data` en lecture seule, sans accès réseau.

```bash
mkdir -p backups
docker compose --profile tools run --rm backup
ls -lh backups/dashboard-data-*.tar.gz
```

Conserver au moins une copie chiffrée hors du VPS. Tester périodiquement une
restauration sur des volumes temporaires ; une archive non testée n’est pas une
garantie de reprise.

## Restauration

La restauration remplace l’état applicatif. Vérifier d’abord le nom exact de
l’archive et conserver une sauvegarde supplémentaire de l’état courant.

```bash
docker compose stop torrent-panel cloud-panel
docker compose --profile tools run --rm backup
docker volume ls | grep dashboard
```

Extraire ensuite l’archive dans des volumes temporaires, contrôler son contenu,
puis seulement recopier les répertoires `torrent-panel/` et `cloud-panel/` vers
les volumes nommés correspondants. Les noms Docker dépendent du nom du projet ;
ne pas automatiser cette étape avec un motif générique.

Après restauration :

```bash
docker compose up -d torrent-panel cloud-panel
docker compose ps
curl -I http://127.0.0.1:3110/healthz
curl -I http://127.0.0.1:3130/healthz
```

## Déploiement

```bash
git pull --ff-only
make check
make audit
docker compose build torrent-panel prowlarr-panel cloud-panel
docker compose up -d
docker compose ps
docker compose logs --tail=100 torrent-panel prowlarr-panel cloud-panel
```

Valider ensuite les endpoints `healthz` et `readyz`, puis les parcours principaux
dans le navigateur. Une erreur de readiness doit être diagnostiquée avant de
déclarer le déploiement terminé.

## Retour arrière

Noter le commit déployé avant chaque mise à jour. En cas de régression :

1. Revenir explicitement au commit précédemment validé dans une copie de travail
   propre.
2. Reconstruire les images depuis ce commit.
3. Relancer les services avec `docker compose up -d`.
4. Restaurer les volumes uniquement si une migration de données incompatible a
   eu lieu ; ne jamais restaurer des données par réflexe.

## Diagnostic

```bash
docker compose ps
docker compose logs --tail=200 torrent-panel prowlarr-panel cloud-panel
systemctl status autossh-ultra.service
systemctl status rclone
ss -ltnp | grep -E '16141|16124|5572'
```

Ne pas publier de logs bruts dans un ticket avant d’avoir vérifié l’absence de
jeton, cookie, passkey, URL privée ou nom de fichier sensible.

## Rotation recommandée

- Sauvegarde quotidienne avec rétention glissante.
- Test de restauration trimestriel.
- Mise à jour mensuelle des images et dépendances, après passage de la CI.
- Rotation immédiate des clés et mots de passe en cas d’exposition suspectée.
