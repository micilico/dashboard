# Instructions IA — implémenter fidèlement le mockup du Dashboard

## Mission

Reproduire dans l'application existante la vue « Vue d'ensemble » montrée dans le mockup fourni par l'utilisateur :

`/var/folders/h3/nq2x8xj905s8sc3_p1ttf_pw0000gn/T/codex-clipboard-07275e22-841e-46fc-9f02-c44312f42427.png`

Le mockup est la référence visuelle principale pour la composition desktop, la hiérarchie, les proportions, les espacements et l'ambiance. Les données affichées doivent néanmoins être réelles et provenir des API existantes.

Il ne s'agit pas d'appliquer quelques nouvelles couleurs à l'interface actuelle. Il faut reconstruire la vue d'ensemble pour qu'elle reprenne réellement la composition du mockup, tout en conservant les écrans fonctionnels Torrents, Prowlarr, Stockage, Médias, Santé et Activité.

## Résultat attendu

À 1600 × 1000 px, la page doit être immédiatement reconnaissable comme l'implémentation du mockup :

- sidebar verticale sombre à gauche ;
- grand en-tête « Tout fonctionne. Parfaitement. » ;
- badge global opérationnel sous le titre ;
- grande carte de stockage avec visualisation orbitale ;
- trois cartes de métriques compactes avec sparklines ;
- carte Services ;
- carte Activité récente ;
- fond nocturne presque noir avec lumière indigo localisée ;
- surfaces satinées, bordures fines et profondeur discrète.

La fidélité de structure et de hiérarchie est prioritaire. Ne pas considérer la tâche terminée si seuls les tokens, les couleurs ou les rayons ont changé.

## Contraintes techniques

Conserver la stack existante : HTML, CSS et JavaScript natifs servis par FastAPI. Ne pas introduire React, Vue, Tailwind ou un framework de composants.

Fichiers à examiner avant toute modification :

- `DESIGN_INSTRUCTIONS.md` ;
- `torrent-panel/torrent_panel/static/index.html` ;
- `torrent-panel/torrent_panel/static/app.js` ;
- `torrent-panel/torrent_panel/static/app.css` ;
- `torrent-panel/torrent_panel/static/css/home.css` ;
- `torrent-panel/torrent_panel/static/css/responsive.css` ;
- `common/css/tokens.css` ;
- `common/css/base.css` ;
- `common/css/components/navigation.css` ;
- `common/css/components/cards.css` ;
- `torrent-panel/torrent_panel/services/monitoring.py` ;
- `torrent-panel/build.py` ;
- les tests frontend et backend du panneau Torrents.

Préserver toutes les fonctions existantes : navigation, ajout de torrent, filtres, recherche, actions de ligne, dialogues, actualisation, alertes, pages secondaires et préfixes publics configurables.

Ne jamais exposer de secret, d'adresse interne sensible, de cookie, de passkey, de clé API ou d'identifiant de connexion dans le HTML, le JavaScript, les réponses API ou les logs.

## Étape obligatoire avant de coder

1. Ouvrir et inspecter le mockup à sa résolution d'origine.
2. Faire l'inventaire des composants visibles et des données nécessaires.
3. Faire correspondre chaque donnée à une API existante.
4. Identifier les données manquantes et étendre proprement l'API `/api/dashboard` si nécessaire.
5. Définir la structure HTML cible avant de modifier le CSS.

Ne pas inventer de valeurs statiques telles que `12,8 To`, `86 Mo/s`, `24` ou `18/18`. Ce sont des exemples visuels du mockup. Utiliser les données réelles et afficher un état de chargement ou indisponible lorsqu'elles manquent.

## Composition desktop obligatoire

### 1. Structure générale

Pour les écrans d'au moins 1024 px :

- sidebar fixe ou sticky d'environ 220 px ;
- contenu principal à droite, fluide, sans grand panneau englobant toute la page ;
- largeur utile maximale proche de 1600 px ;
- gouttières de 32 à 48 px ;
- espace vertical généreux entre l'en-tête et la grille ;
- fond global visible entre les cartes.

La vue d'ensemble ne doit pas être enfermée dans l'actuel grand `.content-panel` uniforme. Les cartes doivent exister comme surfaces indépendantes sur le fond principal, comme dans le mockup.

### 2. Sidebar

Reproduire la hiérarchie du mockup :

- logo original vectoriel en haut ;
- navigation principale au centre ;
- état global et dernière mise à jour en bas ;
- entrée active avec fond indigo doux, texte clair et barre verticale indigo ;
- icônes outline cohérentes de 20 à 22 px ;
- libellés : Vue d'ensemble, Torrents, Prowlarr, Médias et Système, ou les destinations existantes équivalentes si elles doivent rester séparées.

Ne pas utiliser les carrés génériques actuellement créés avec `.nav a::before`. Utiliser de vrais SVG inline accessibles issus d'une seule famille visuelle. Les logos de services peuvent utiliser leur identité officielle si les assets sont disponibles et correctement licenciés ; sinon utiliser des pictogrammes cohérents sans contrefaire les logos.

La sidebar doit sembler intégrée au bord gauche. Éviter l'apparence de grande carte flottante arrondie si elle éloigne le rendu du mockup.

### 3. En-tête principal

L'en-tête contient :

- une salutation discrète en capitales espacées, personnalisée si le nom est disponible ;
- un `h1` sur deux lignes : « Tout fonctionne. » puis « Parfaitement. » lorsque l'état est sain ;
- un texte alternatif explicite en cas d'incident, par exemple « Une attention est requise. » ;
- un badge d'état global sous le titre ;
- en haut à droite, deux boutons circulaires : recherche et profil/paramètres si ces actions existent réellement.

Le `h1` desktop doit approcher 64 px, avec une graisse 650–700, un interligne proche de 1,02 et un léger tracking négatif. Ne pas rendre ce titre comme un simple `h2` de 24 px.

Le badge sain utilise un point ou une icône, le texte « Tous les services sont opérationnels » et le vert menthe. La couleur ne doit pas être le seul indicateur.

Si les actions recherche/profil n'ont aucune fonctionnalité réelle, ne pas créer de boutons factices. Remplacer par une action existante utile, par exemple recherche globale et actualisation, avec `aria-label` et infobulle.

### 4. Grille principale

Construire une grille asymétrique en deux zones :

- colonne gauche : grande carte « Votre espace média », environ 34 % de la largeur de la grille ;
- colonne droite : environ 66 %, avec trois métriques sur la première rangée, puis Services et Activité récente sur la seconde.

Proportions desktop recommandées :

```css
grid-template-columns: minmax(340px, 0.95fr) minmax(0, 1.85fr);
gap: 20px;
```

Dans la zone droite :

```css
grid-template-columns: repeat(6, minmax(0, 1fr));
```

- chaque métrique occupe deux colonnes ;
- Services occupe quatre colonnes ;
- Activité récente occupe deux colonnes.

Adapter les valeurs si nécessaire pour atteindre visuellement les proportions du mockup. Ne pas forcer ces nombres si une légère variation donne une meilleure fidélité à 1440 et 1600 px.

### 5. Carte « Votre espace média »

Cette carte est le point focal visuel et doit contenir :

- titre en haut à gauche ;
- menu d'options accessible en haut à droite seulement si une action existe ;
- visualisation orbitale au centre ;
- capacité libre en grand format ;
- libellé « disponibles » ;
- barre de progression ;
- texte « X utilisés / Y au total ».

Connecter la carte à `/api/storage` ou inclure dans `/api/dashboard` un résumé de stockage contenant au minimum :

- `totalBytes` ;
- `usedBytes` ;
- `freeBytes` ;
- `usedPercent` ;
- `status` ;
- `generatedAt`.

La visualisation orbitale doit être produite en SVG ou en CSS, pas par une grosse image raster opaque. Elle doit rester nette, responsive et légère. Elle peut être décorative, mais la valeur de stockage et la barre de progression doivent rester la source d'information principale.

Exigences pour la visualisation :

- planète/sphère centrale avec dégradé indigo sombre ;
- deux ou trois orbites elliptiques fines ;
- arc lumineux indiquant visuellement l'espace utilisé ou disponible ;
- quelques satellites discrets ;
- pas de canvas lourd ni de bibliothèque 3D ;
- SVG décoratif avec `aria-hidden="true"` ;
- animation facultative, très lente, uniquement via `transform`/`opacity` ;
- aucune animation lorsque `prefers-reduced-motion: reduce` est actif.

Prévoir les états chargement, erreur, disque non monté et valeur inconnue. Une erreur ne doit jamais laisser la carte vide.

### 6. Cartes de métriques

Créer exactement trois cartes compactes :

1. Débit — valeur de téléchargement actuelle en Mo/s ou unité adaptée ;
2. Torrents actifs — nombre de torrents actifs ;
3. Indexeurs — nombre opérationnel / nombre total.

Chaque carte contient :

- une icône outline dans un petit conteneur ;
- un label secondaire ;
- une valeur dominante ;
- une sparkline fine en bas ;
- une unité explicite ;
- un texte accessible résumant la tendance.

Ne pas afficher les cinq anciennes statistiques globales au-dessus de la vue d'ensemble. Elles peuvent rester dans la vue Torrents, où elles sont pertinentes.

Les sparklines doivent représenter de vraies données historiques si elles sont disponibles. Sinon :

- créer côté client un petit historique glissant en mémoire à partir des rafraîchissements successifs ;
- limiter le nombre de points, par exemple 12 à 20 ;
- ne pas inventer une tendance au premier chargement ;
- afficher une ligne neutre ou un état « historique en cours » ;
- utiliser un SVG responsive avec `preserveAspectRatio="none"` ;
- fournir un résumé textuel accessible.

### 7. Carte Services

Afficher en priorité Jellyfin, qBittorrent, Prowlarr et rclone. Chaque ligne contient :

- icône ou logo ;
- nom ;
- statut textuel ;
- point/forme d'état ;
- chevron si la ligne ouvre une page de détail ;
- dernier contrôle dans le nom accessible ou dans le détail si l'espace le permet.

La carte doit ressembler à une liste calme, avec séparateurs subtils, et non à une grille de petites cartes imbriquées.

Les statuts doivent utiliser les mêmes mots partout : Opérationnel, Dégradé, En attente, Indisponible, Désactivé.

Ajouter une action « Tout voir » menant à Santé uniquement si la destination fonctionne.

### 8. Carte Activité récente

Afficher trois événements récents au maximum dans la vue synthétique :

- icône sémantique ;
- titre court ;
- service ou détail ;
- temps relatif ;
- séparateur subtil entre événements.

Utiliser les données réelles de `/api/activity`. Ne pas laisser le panneau vide si l'API échoue : afficher une erreur compacte avec une action Réessayer.

Ajouter « Tout voir » vers la page Activité. Grouper les événements répétitifs si nécessaire.

## Direction visuelle obligatoire

Réutiliser et compléter les tokens sémantiques communs. Ne pas disperser de nouvelles couleurs hexadécimales dans les composants.

Base recommandée :

```css
--bg: #07080b;
--bg-secondary: #0b0d12;
--surface: #101217;
--surface-2: #151821;
--surface-interactive: #1a1e29;
--text: #f5f5f7;
--muted: #a7abb5;
--text-subtle: #7e8491;
--border: rgba(255, 255, 255, 0.09);
--accent: #7c6cff;
--accent-soft: rgba(124, 108, 255, 0.14);
--success: #5ee6a8;
--warning: #f4bd62;
--danger: #ff6b72;
--focus: #a89dff;
```

Règles visuelles :

- fond presque noir avec une lumière indigo très localisée ;
- cartes légèrement plus claires que le fond ;
- bordures de 1 px maximum ;
- rayons proches de 20 à 24 px ;
- ombres diffuses et peu opaques ;
- glow réservé à la visualisation orbitale, au focus et aux données indigo ;
- aucune grande bordure claire ;
- aucun néon généralisé ;
- aucun emoji comme icône ;
- Inter Variable auto-hébergée avec `font-display: swap` ;
- chiffres tabulaires pour toutes les métriques.

## Responsive obligatoire

Tester au minimum à 375, 768, 1024, 1440 et 1600 px.

### 768–1023 px

- sidebar compacte de 72 à 80 px ;
- icônes visibles et libellés accessibles via nom accessible/infobulle ;
- carte stockage en pleine largeur ou première ligne ;
- métriques sur deux ou trois colonnes selon l'espace ;
- Services et Activité sur deux colonnes si possible.

### Moins de 768 px

- en-tête compact ;
- navigation mobile claire, sans sept éléments minuscules dans une rangée ;
- une seule colonne ;
- ordre : état global, stockage, métriques, services, activité ;
- cartes avec padding réduit à 16–20 px ;
- aucune barre de défilement horizontale globale ;
- toutes les cibles interactives mesurent au moins 44 × 44 px ;
- la visualisation orbitale se simplifie sans masquer les valeurs ;
- le `h1` descend autour de 38–42 px.

Ne pas simplement réduire toute la composition desktop. Concevoir réellement les variantes tablette et mobile.

## Accessibilité

- Conserver le lien d'évitement « Aller au contenu ».
- Utiliser un unique `h1`, puis une hiérarchie de titres séquentielle.
- Ajouter un nom accessible à tous les boutons icône.
- Utiliser `aria-current="page"` uniquement sur la destination active ; retirer l'attribut ailleurs plutôt que définir `aria-current="false"`.
- Conserver un focus clavier visible d'au moins 2 px.
- Garantir un contraste de 4,5:1 pour le texte normal et 3:1 pour le grand texte et les éléments graphiques essentiels.
- Ne jamais communiquer un état uniquement par la couleur.
- Utiliser `aria-live="polite"` pour les mises à jour d'état non urgentes.
- Les graphiques décoratifs sont masqués aux technologies d'assistance ; leur information utile existe aussi en texte.
- Respecter `prefers-reduced-motion`.
- Vérifier la navigation complète au clavier.

## États et comportement

La vue doit gérer explicitement :

- chargement initial avec espace réservé/skeleton sans saut de mise en page ;
- état entièrement opérationnel ;
- services dégradés ;
- incident critique ;
- API partiellement indisponible ;
- absence d'activité ;
- stockage inconnu ou non monté ;
- rafraîchissement en cours ;
- données anciennes.

En cas d'incident, modifier le texte principal, le badge et les cartes concernées. Ne pas seulement passer un point du vert au rouge.

Afficher une heure de dernière vérification réelle et signaler les données anciennes selon une règle explicite.

## Données et API

Réutiliser les fonctions existantes dans `torrent-panel/torrent_panel/services/monitoring.py` au lieu de dupliquer les requêtes réseau.

Faire évoluer `/api/dashboard` pour renvoyer une vue synthétique cohérente, ou charger en parallèle les endpoints spécialisés si cela ne crée pas de cascade lente. Éviter de refaire plusieurs fois les mêmes contrôles distants dans un seul rafraîchissement.

Le contrat frontend devrait disposer au minimum de :

```json
{
  "generatedAt": "ISO-8601",
  "globalStatus": "operational",
  "criticalCount": 0,
  "metrics": {
    "downloadSpeedBytes": 0,
    "activeTorrents": 0,
    "indexersOperational": 0,
    "indexersTotal": 0
  },
  "storage": {
    "status": "normal",
    "totalBytes": 0,
    "usedBytes": 0,
    "freeBytes": 0,
    "usedPercent": 0
  },
  "services": [],
  "recentActivity": [],
  "alerts": []
}
```

Ce schéma est une cible, pas une obligation de nommage si les conventions existantes imposent autre chose. Ajouter ou adapter les tests backend pour figer le contrat retenu.

## Build et cache — correction obligatoire

La page charge actuellement `static/dist/app.min.css` et `static/dist/app.min.js`. Les sources et les bundles doivent toujours correspondre.

Corriger le processus de build afin que :

- `python torrent-panel/build.py` reconstruise les bundles sans dupliquer plusieurs fois les mêmes modules CSS ;
- les fichiers `dist` générés contiennent les nouvelles règles et le nouveau JavaScript ;
- le build échoue si un import CSS ne peut pas être résolu ;
- le cachebuster `?v=BUILD` soit remplacé par une version réelle ou une empreinte de contenu au moment du build/de la réponse ;
- le navigateur ne conserve pas silencieusement une ancienne interface après déploiement ;
- Docker reconstruise effectivement les assets lors de la création de l'image.

Ne pas conclure que l'interface est livrée parce que les fichiers source ont changé. Vérifier les assets réellement servis par HTTP.

## Tests automatisés obligatoires

Ajouter ou adapter des tests couvrant au minimum :

1. la vue d'ensemble contient un `h1` et les cinq zones : stockage, trois métriques, services, activité ;
2. les anciennes cinq statistiques Torrents ne sont pas affichées au-dessus de la vue d'ensemble ;
3. les métriques utilisent les données API et non des valeurs codées en dur ;
4. la carte stockage gère valeurs normales, inconnues et disque non monté ;
5. le titre et le badge changent explicitement en cas d'incident ;
6. les services affichent un statut textuel ;
7. une erreur d'activité produit un état récupérable et non une carte vide ;
8. les SVG décoratifs sont masqués aux technologies d'assistance ;
9. les boutons icône possèdent un nom accessible ;
10. `aria-current` n'existe que sur le lien actif ;
11. les liens configurés respectent les préfixes publics ;
12. les bundles reconstruits contiennent les nouveaux sélecteurs et ne dupliquent pas les modules CSS ;
13. le cachebuster servi change lorsque les assets changent ;
14. les fonctions Torrents, recherche, filtres et dialogues existantes ne régressent pas.

Exécuter au minimum :

- les tests frontend du panneau Torrents ;
- les tests backend du panneau Torrents ;
- les tests du panneau Prowlarr si des styles ou composants communs sont modifiés ;
- le script de build des deux panneaux concernés.

## Validation visuelle obligatoire

Cette étape est bloquante pour la livraison.

1. Lancer l'application avec des données réelles ou des fixtures représentatives.
2. Capturer la vue d'ensemble à 1600 × 1000 px.
3. Comparer la capture côte à côte avec le mockup original.
4. Corriger au minimum : proportions, alignements, tailles de titre, espaces, hauteurs de cartes, densité, contrastes et ordre visuel.
5. Répéter jusqu'à ce que la composition soit clairement la même.
6. Capturer également 1440 px, 1024 px, 768 px et 375 px.
7. Vérifier qu'aucun contenu ne déborde et qu'aucune information utile n'est coupée.

Une simple inspection du code n'est pas une validation visuelle. Une IA doit impérativement rendre la page dans un navigateur et examiner les captures.

## Écarts autorisés par rapport au mockup

Sont autorisés :

- les valeurs numériques réelles ;
- les textes d'incident correspondant à l'état réel ;
- le remplacement d'une action fictive par une action fonctionnelle équivalente ;
- une simplification raisonnable de l'illustration orbitale sur mobile ;
- de petites adaptations de proportions nécessaires aux breakpoints ;
- l'usage d'icônes cohérentes à la place de logos indisponibles.

Ne sont pas autorisés :

- revenir à une grille générique de cinq cartes identiques ;
- conserver un grand panneau uniforme entourant toute la page ;
- laisser la carte stockage vide ;
- supprimer les sparklines sans état alternatif ;
- utiliser des emojis ;
- inventer des données ;
- ajouter des boutons sans fonction ;
- déclarer la tâche terminée sans captures de comparaison ;
- ignorer les bundles `dist` ou le cache du navigateur.

## Définition de terminé

La tâche est terminée uniquement lorsque :

- la page servie ressemble réellement au mockup à 1600 × 1000 px ;
- toutes les zones utilisent des données réelles ou des états explicites ;
- desktop, tablette et mobile sont utilisables ;
- le clavier et les technologies d'assistance disposent de noms, rôles et états corrects ;
- les tests frontend et backend passent ;
- les bundles ont été reconstruits et vérifiés via HTTP ;
- le cachebuster est fonctionnel ;
- aucune fonction existante n'a régressé ;
- le compte rendu final fournit les fichiers modifiés, les tests exécutés et les captures utilisées pour la validation.

## Format du compte rendu final demandé à l'IA

Répondre avec :

1. un résumé de l'implémentation ;
2. les principaux choix visuels et les éventuels écarts justifiés au mockup ;
3. les contrats API ajoutés ou modifiés ;
4. la liste des tests et leur résultat ;
5. les dimensions des captures vérifiées ;
6. les problèmes restants, s'il y en a, sans les masquer.
