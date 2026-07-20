# Instructions IA — corrections navigation, recherche torrents et santé SSH

## Mission

Corriger les trois problèmes ci-dessous dans le dashboard, sans régression sur les fonctions existantes et sans exposer de secret, d’URL interne sensible ou d’identifiant de connexion dans le frontend ou les logs.

Avant de modifier le code, lire les fichiers concernés et conserver le style visuel, les composants et les conventions déjà en place. Ne pas refaire l’interface entière.

## 1. Uniformiser « Vue d’ensemble » dans toute l’application

### Problème constaté

Depuis Prowlarr, le lien « Vue d’ensemble » renvoie vers `/`, donc vers le dashboard Homepage initial. Depuis Torrents, le même libellé ouvre la vue d’ensemble du centre de contrôle via `/torrent-panel/?view=home`. Deux liens portant le même nom ont donc deux comportements différents.

### Comportement attendu

- « Vue d’ensemble » doit toujours ouvrir la même vue globale du centre de contrôle : `/torrent-panel/?view=home`.
- Ce comportement doit être identique depuis Torrents, Prowlarr, Stockage, Médias, Santé et Activité.
- Le lien actif doit recevoir `aria-current="page"` uniquement lorsque la vue globale est affichée.
- Si un accès au dashboard Homepage initial doit être conservé, l’exposer sous un libellé distinct et non ambigu, par exemple « Accueil services ». Ne jamais utiliser « Vue d’ensemble » pour deux destinations différentes.
- Les liens doivent continuer à fonctionner lorsque les préfixes publics sont configurés par variables d’environnement. Éviter de coder une URL absolue en dur si une valeur de configuration existe déjà.

### Fichiers à examiner en priorité

- `prowlarr-panel/prowlarr_panel/static/index.html`
- `prowlarr-panel/prowlarr_panel/static/app.js`
- `prowlarr-panel/prowlarr_panel/main.py`
- `torrent-panel/torrent_panel/static/index.html`
- `torrent-panel/torrent_panel/static/console.js`
- `torrent-panel/torrent_panel/main.py`

### Détail important

Le lien Prowlarr `#homeLink` pointe actuellement vers `/`. Le corriger pour utiliser le préfixe public du panneau Torrents et la query string `?view=home`. Dans les pages communes pilotées par `console.js`, corriger également `configureLinks()`, qui assigne actuellement `/` à `homeLink`.

## 2. Ajouter une recherche torrent visible et fonctionnelle

### Problème constaté

L’utilisateur ne dispose pas, dans l’interface effectivement affichée, d’une barre de recherche clairement accessible pour retrouver un torrent.

Le dépôt contient déjà `#searchInput` et une logique `prefs.search`/`filteredTorrents()` dans le panneau Torrents. Ne pas ajouter un second champ concurrent : vérifier pourquoi le champ existant n’est pas visible ou disponible dans la version rendue, puis finaliser cette fonction.

### Comportement attendu

- Afficher une barre de recherche clairement identifiable dans la vue Torrents, au-dessus de la liste et avant les filtres secondaires.
- Utiliser un vrai `input type="search"` avec un label visible « Rechercher un torrent » et un placeholder utile, par exemple « Nom, catégorie ou tag ».
- Filtrer localement la liste déjà chargée, sans requête réseau à chaque frappe.
- La recherche doit être insensible à la casse et aux accents.
- La recherche doit porter au minimum sur le nom du torrent et, si les données sont disponibles, sur sa catégorie et ses tags.
- Mettre à jour les résultats pendant la saisie avec un délai court si nécessaire, sans latence perceptible.
- Afficher le nombre de résultats et un état vide explicite : `Aucun torrent ne correspond à « … »`.
- Ajouter un bouton accessible pour effacer la recherche lorsqu’elle n’est pas vide.
- Conserver la recherche dans l’URL (`search=...`) afin que l’état soit partageable et restauré au rechargement, comme le prévoit déjà la logique existante.
- Le bouton global « Effacer les filtres » et le bouton « Réinitialiser » doivent aussi vider la recherche.
- Sur mobile, le champ doit rester visible, occuper la largeur disponible et avoir une hauteur tactile d’au moins 44 px.
- La navigation clavier, le focus visible et le nom accessible doivent être préservés.

### Fichiers à examiner en priorité

- `torrent-panel/torrent_panel/static/index.html`
- `torrent-panel/torrent_panel/static/app.js`
- `torrent-panel/torrent_panel/static/app.css`
- `torrent-panel/tests/frontend_logic.test.js`

### Vérification de livraison

Le HTML contient déjà le champ et le JavaScript contient déjà une partie de la logique. Vérifier aussi les versions/cachebusters des fichiers statiques et la construction Docker afin que la version déployée serve bien la correction. Ne pas conclure que la tâche est terminée uniquement parce que le champ existe dans le dépôt.

## 3. Supprimer les faux négatifs sur les tunnels SSH qBittorrent et Prowlarr

### Problème constaté

La page Santé indique que les tunnels SSH qBittorrent et Prowlarr sont hors service alors que les deux applications fonctionnent réellement à travers ces tunnels.

La logique actuelle crée deux états indépendants via `socket_service_status()` et `asyncio.open_connection(host, port)`. Ce test TCP brut peut échouer depuis le conteneur ou viser une configuration différente, alors qu’une requête applicative qBittorrent ou Prowlarr a déjà réussi. Il ne doit pas contredire une preuve fonctionnelle plus forte.

### Comportement attendu

- Utiliser en priorité un contrôle fonctionnel de bout en bout :
  - tunnel qBittorrent opérationnel si l’authentification et/ou la récupération de la liste des torrents réussit via `app.state.qbit` ;
  - tunnel Prowlarr opérationnel si `Prowlarr Panel` confirme sa readiness et si son endpoint d’overview indique `connection == "ready"`.
- Un test TCP brut peut rester disponible comme diagnostic secondaire, mais il ne doit jamais afficher le tunnel « indisponible » si le contrôle fonctionnel correspondant réussit.
- Éviter les vérifications dupliquées qui donnent deux états contradictoires pour un même service.
- Si le contrôle applicatif réussit mais que le test TCP brut échoue, afficher au maximum une information de diagnostic non critique, sans créer d’alerte rouge ni dégrader l’état global.
- Si le contrôle applicatif échoue, distinguer autant que possible : tunnel/réseau inaccessible, authentification refusée, application distante non prête ou endpoint mal configuré. Ne pas qualifier automatiquement tout échec applicatif de panne SSH.
- Les hôtes et ports de diagnostic doivent rester configurables avec `TORRENT_PANEL_QBIT_TUNNEL_HOST`, `TORRENT_PANEL_QBIT_TUNNEL_PORT`, `TORRENT_PANEL_PROWLARR_TUNNEL_HOST` et `TORRENT_PANEL_PROWLARR_TUNNEL_PORT`.
- Ne jamais renvoyer les mots de passe, clés API, cookies, passkeys ou URL contenant des secrets.
- Les alertes persistées doivent être résolues/considérées stables lorsqu’un nouveau contrôle confirme que le service fonctionne, afin de ne pas continuer à afficher une ancienne panne comme active.

### Fichiers à examiner en priorité

- `torrent-panel/torrent_panel/main.py`, notamment `socket_service_status()`, `dashboard_snapshot()` et `health_snapshot()`
- `torrent-panel/torrent_panel/static/console.js`
- `torrent-panel/tests/test_backend.py`
- `docker-compose.yml`
- les exemples de configuration `.env` s’ils sont présents

## Tests obligatoires

Ajouter ou adapter les tests automatisés pour couvrir au minimum :

1. Tous les liens « Vue d’ensemble » des panneaux personnalisés résolvent vers `/torrent-panel/?view=home` ou vers le préfixe configuré équivalent.
2. La recherche trouve un torrent par nom sans tenir compte de la casse.
3. La recherche trouve un torrent par catégorie ou tag.
4. La recherche gère les accents de façon tolérante.
5. La query string `search` restaure le champ et le filtrage après rechargement.
6. Une recherche sans résultat affiche un état vide explicite.
7. Si l’appel fonctionnel qBittorrent réussit mais que le test TCP brut échoue, le tunnel qBittorrent reste opérationnel et aucune alerte critique SSH n’est créée.
8. Si l’overview Prowlarr retourne `connection: ready` mais que le test TCP brut échoue, le tunnel Prowlarr reste opérationnel et aucune alerte critique SSH n’est créée.
9. Si les contrôles fonctionnels et réseau échouent réellement, l’état indisponible et l’alerte correspondante sont bien affichés.

Exécuter au minimum les suites frontend et backend existantes des deux panneaux. Corriger toute régression liée aux changements.

## Critères d’acceptation manuels

- Depuis Prowlarr, cliquer sur « Vue d’ensemble » ouvre exactement la même page que depuis Torrents.
- La destination ne change pas selon la page de départ.
- Dans Torrents, la recherche est immédiatement visible sans ouvrir un panneau secondaire.
- Saisir une partie du nom réduit instantanément la liste ; effacer le champ restaure tous les torrents.
- La recherche reste utilisable sur mobile et au clavier.
- Si qBittorrent et Prowlarr répondent normalement, la page Santé n’affiche aucun des deux tunnels SSH comme hors service.
- Une vraie coupure reste détectée et expliquée sans exposer d’information sensible.

## Livrable attendu

Fournir les modifications de code, les tests ajoutés ou mis à jour, le résultat des commandes de test et un court résumé des causes corrigées. Ne pas masquer artificiellement les alertes : corriger leur source et leur règle de priorité.
