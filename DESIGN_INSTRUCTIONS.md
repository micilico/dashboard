# Instructions de design — Dashboard premium

## 1. Ambition

L’interface doit atteindre le niveau de soin d’un produit technologique haut de gamme : hiérarchie immédiate, typographie irréprochable, espaces généreux, transitions fluides et absence de bruit visuel.

La référence Apple sert uniquement de niveau d’exigence. Ne pas reproduire sa marque, ses mises en page ou ses composants. Le Dashboard doit conserver une identité originale, plus technique et plus nocturne.

Principes directeurs :

- faire comprendre l’état du système en moins de trois secondes ;
- mettre les informations importantes en avant et révéler le détail progressivement ;
- donner une impression de calme, même lorsque beaucoup de données sont disponibles ;
- privilégier la précision et la cohérence aux effets décoratifs ;
- conserver toutes les fonctions actuelles et les rendre plus faciles à parcourir ;
- traiter desktop et mobile comme deux compositions adaptées, pas comme un simple redimensionnement.

## 2. Direction visuelle

Style : minimalisme sombre premium, surfaces satinées, profondeur légère et bento grid asymétrique.

L’interface ne doit pas ressembler à un template d’administration générique. Elle doit évoquer un centre de contrôle personnel, silencieux et très soigné.

À utiliser :

- fond presque noir avec une très légère lumière indigo localisée ;
- grandes zones de respiration autour des titres et des blocs principaux ;
- surfaces sombres différenciées par la luminance, pas par de gros contours ;
- accent indigo réservé à la navigation, aux sélections et aux données importantes ;
- vert menthe réservé aux états positifs ;
- icônes vectorielles simples, toutes issues de la même famille ;
- données numériques en chiffres tabulaires ;
- graphiques fins et sobres, sans décoration superflue.

À éviter :

- néons cyberpunk, glow généralisé ou dégradés multicolores ;
- glassmorphism sur chaque carte ;
- bordures claires et épaisses ;
- ombres fortes ou noires autour de tous les blocs ;
- petites cartes identiques alignées sans hiérarchie ;
- emojis comme icônes ;
- texte secondaire trop gris ou inférieur à 12 px ;
- animations permanentes purement décoratives ;
- imitation de composants, textes, logos ou visuels Apple.

## 3. Design tokens

Toutes les valeurs doivent être définies sous forme de tokens sémantiques partagés. Aucun écran ne doit inventer ses propres couleurs, rayons ou ombres.

### Couleurs

| Rôle | Valeur de départ | Usage |
|---|---:|---|
| Fond principal | `#07080B` | arrière-plan global |
| Fond secondaire | `#0B0D12` | zones de navigation |
| Surface 1 | `#101217` | cartes principales |
| Surface 2 | `#151821` | cartes surélevées, contrôles |
| Surface interactive | `#1A1E29` | survol, sélection légère |
| Texte principal | `#F5F5F7` | titres et contenu essentiel |
| Texte secondaire | `#A7ABB5` | descriptions et métadonnées |
| Texte discret | `#7E8491` | informations tertiaires |
| Bordure | `rgba(255,255,255,.09)` | séparation subtile |
| Accent | `#7C6CFF` | sélection, navigation, CTA principal |
| Accent doux | `rgba(124,108,255,.14)` | fond sélectionné |
| Succès | `#5EE6A8` | service opérationnel |
| Avertissement | `#F4BD62` | état dégradé ou attente |
| Danger | `#FF6B72` | panne et action destructive |
| Focus | `#A89DFF` | anneau clavier visible |

Ces valeurs sont une base. Avant implémentation, vérifier toutes les paires avec WCAG : 4,5:1 pour le texte normal, 3:1 pour le grand texte et les éléments graphiques indispensables.

La couleur ne doit jamais être l’unique moyen de communiquer un état : toujours associer libellé, icône ou forme.

### Typographie

- Police principale : `Inter Variable`, auto-hébergée si possible.
- Repli : `Inter`, `-apple-system`, `BlinkMacSystemFont`, `Segoe UI`, sans-serif.
- Ne pas mélanger plusieurs familles typographiques.
- Graisses autorisées : 400, 500, 600 et 700 uniquement.
- Utiliser `font-variant-numeric: tabular-nums` pour les débits, tailles, durées, ratios et compteurs.
- Ne pas resserrer le tracking du texte courant.

Échelle recommandée :

| Style | Desktop | Mobile | Interligne |
|---|---:|---:|---:|
| Display | 64 px | 40 px | 1,02 |
| Titre de page | 40 px | 32 px | 1,1 |
| Titre de section | 24 px | 22 px | 1,25 |
| Titre de carte | 18 px | 18 px | 1,35 |
| Corps | 16 px | 16 px | 1,55 |
| Métadonnée | 14 px | 14 px | 1,45 |
| Label | 12 px | 12 px | 1,35 |

Le grand display est réservé à l’accueil. Les écrans fonctionnels utilisent un titre de page plus compact afin de préserver la densité utile.

### Espacements et géométrie

- Échelle d’espacement : 4, 8, 12, 16, 24, 32, 48, 64, 96 px.
- Gouttière desktop : 32 px ; grand écran : 48 px ; mobile : 16 px.
- Écart standard entre cartes : 16 px ; 20 à 24 px sur grand écran.
- Padding d’une carte standard : 24 px ; carte héro : 32 px.
- Rayons : 12 px pour les contrôles, 18 px pour les petits panneaux, 24 px pour les cartes et 32 px pour la carte héro.
- Hauteur interactive minimale : 44 px.
- Largeur de contenu maximale : 1600 px, hors sidebar.

### Bordures, ombres et lumière

- Une bordure de 1 px maximum par surface.
- Ombre standard : très diffuse, faible opacité, uniquement pour détacher un panneau important.
- Glow indigo autorisé seulement sur la carte héro, le focus ou un état actif.
- Le flou d’arrière-plan est réservé aux dialogues, feuilles mobiles et panneaux superposés.
- Ajouter éventuellement un grain global presque imperceptible ; il ne doit jamais réduire la lisibilité.

## 4. Structure globale

### Desktop, à partir de 1024 px

- Sidebar fixe ou sticky de 224 px environ.
- Logo original en haut, navigation principale au centre, état global et dernière mise à jour en bas.
- Zone principale fluide avec un en-tête aéré puis une grille de contenu.
- Actions globales en haut à droite : recherche, actualisation ou profil selon le contexte.
- Une seule action primaire visible par écran.

Navigation principale :

1. Vue d’ensemble
2. Torrents
3. Prowlarr
4. Stockage
5. Médias
6. Santé ou Activité

Chaque entrée possède une icône outline de 20 à 22 px et un libellé. L’état actif utilise un fond indigo doux, un texte clair et un indicateur latéral. Ne pas utiliser la couleur seule.

### Tablette, de 768 à 1023 px

- Sidebar compacte de 72 à 80 px avec libellés accessibles via une infobulle et un nom accessible.
- Grille principale en deux colonnes.
- Les actions secondaires passent dans un menu de débordement.

### Mobile, en dessous de 768 px

- En-tête compact avec titre, état global et action principale.
- Navigation dans un panneau latéral accessible ou une barre inférieure limitée aux cinq destinations les plus importantes.
- Contenu en une colonne, sans défilement horizontal global.
- Les tableaux deviennent des cartes structurées ; conserver un tableau horizontal uniquement quand la comparaison entre colonnes est essentielle.
- Les zones tactiles restent au minimum à 44 × 44 px avec 8 px entre deux actions.

Points de contrôle obligatoires : 375, 768, 1024 et 1440 px.

## 5. Écran « Vue d’ensemble »

Objectif : donner confiance et permettre de détecter immédiatement une anomalie.

Ordre de lecture :

1. salutation discrète ;
2. phrase synthétique d’état, par exemple « Tout fonctionne. Parfaitement. » ;
3. badge d’état global avec heure de la dernière vérification ;
4. métrique ou visualisation principale de stockage ;
5. trois métriques compactes : débit, torrents actifs, indexeurs ;
6. état détaillé des services ;
7. activité récente ;
8. alertes et actions rapides uniquement si elles sont pertinentes.

La carte « Votre espace média » est le point focal. Sa visualisation doit être légère et utile : capacité disponible, capacité totale et progression. Elle peut contenir une animation très lente uniquement si elle s’arrête avec `prefers-reduced-motion` et ne détourne pas l’attention.

Les services affichent : icône officielle ou cohérente, nom, statut textuel, dernier contrôle et chevron si la ligne ouvre un détail.

En cas d’incident, le grand titre et le badge changent de contenu, pas seulement de couleur. L’alerte explique la cause connue et propose une action claire telle que « Réessayer » ou « Ouvrir Santé ».

## 6. Écran « Torrents »

Cet écran est plus dense que l’accueil, mais doit conserver le même langage visuel.

- Titre de page compact, résumé des débits et action primaire « Ajouter un torrent ».
- Statistiques clés en bande horizontale, sans multiplier les cartes.
- Recherche et filtres regroupés dans une barre unique et sticky si la liste est longue.
- Filtres secondaires dans une zone repliable.
- Les filtres actifs apparaissent sous forme de chips supprimables.
- Le tableau desktop reste la vue principale, avec en-tête sticky, colonnes alignées et lignes de 56 à 64 px.
- Les nombres sont alignés à droite, les noms à gauche et les états au centre si cela facilite la lecture.
- La progression associe barre, pourcentage et libellé d’état.
- Les actions de ligne les plus fréquentes restent visibles ; les autres passent dans un menu.
- La sélection multiple fait apparaître une barre d’actions contextuelle stable, sans déplacer brutalement la liste.
- Sur mobile, chaque torrent devient une carte : nom, progression, état, vitesse, ETA et menu d’actions.

La suppression de fichiers reste une action fortement destructive : dialogue dédié, description précise de l’impact et confirmation renforcée. Le focus doit être placé dans le dialogue puis restitué au déclencheur lors de la fermeture.

## 7. Écran « Prowlarr »

- Conserver les vues Indexeurs, Recherche, Applications et Santé comme navigation secondaire.
- Utiliser des tabs sobres sous le titre ; ils ne doivent pas concurrencer la navigation principale.
- Mettre en avant le nombre d’indexeurs opérationnels et l’heure du dernier test.
- Les filtres suivent exactement le même composant que l’écran Torrents.
- Les états « opérationnel », « désactivé », « avertissement » et « erreur » partagent les mêmes badges dans tout le Dashboard.
- La recherche de releases sépare clairement la requête, les catégories, les indexeurs et les résultats.
- Révéler les paramètres avancés progressivement.
- L’action de récupération d’une release doit donner un retour immédiat : chargement, succès ou message de résolution.

## 8. Stockage, Médias, Santé et Activité

### Stockage

- Capacité disponible comme métrique principale.
- Débits, transferts et erreurs comme métriques secondaires.
- Graphique temporel seulement si les données décrivent une évolution ; sinon préférer des valeurs directes.
- Toujours afficher l’unité et la période.

### Médias

- Contenu d’abord : jaquettes ou éléments en cours de lecture si les données existent.
- État des bibliothèques et dernier scan en second niveau.
- Les visuels média doivent avoir des dimensions réservées pour éviter les sauts de layout.

### Santé

- Résumé clair : sain, dégradé ou indisponible.
- Classer les incidents par sévérité et récence.
- Chaque erreur indique la cause, l’impact et une action de récupération.
- Ne jamais afficher une zone vide quand une requête échoue.

### Activité

- Chronologie lisible avec heure, service, action et résultat.
- Grouper les événements répétitifs.
- Offrir une vue détaillée sans surcharger la liste initiale.

## 9. Composants communs

Tous les écrans doivent réutiliser les mêmes composants et les mêmes états.

### Boutons

- Primaire : fond indigo, un seul par zone de décision.
- Secondaire : surface sombre et bordure subtile.
- Tertiaire : texte ou icône sans conteneur lourd.
- Danger : rouge uniquement pour une action réellement destructive.
- États obligatoires : repos, survol, pressé, focus, chargement, désactivé.
- Pendant une requête, désactiver le bouton et afficher un indicateur sans changer sa largeur.

### Cartes

- Une carte doit représenter un groupe logique, pas seulement décorer une donnée.
- Titre, contenu et éventuelle action doivent garder le même alignement.
- Les cartes cliquables ont un état de survol, un curseur adapté et un focus visible.
- Ne pas appliquer d’effet de survol aux cartes non interactives.

### Badges d’état

- Inclure une forme ou icône, un texte explicite et une couleur sémantique.
- Utiliser les mêmes mots partout : « Opérationnel », « Dégradé », « En attente », « Indisponible », « Désactivé ».

### Formulaires et filtres

- Chaque champ a un label visible.
- Les placeholders donnent un exemple, ils ne remplacent jamais le label.
- Validation au blur ou à la soumission, avec message au plus près du champ.
- Les erreurs indiquent le problème et la manière de le résoudre.
- Les contrôles complexes utilisent la divulgation progressive.

### Tableaux

- En-têtes clairement distingués et tri accessible avec `aria-sort`.
- Alignement cohérent par type de donnée.
- Survol discret, focus clavier visible et sélection non ambiguë.
- États chargement, vide et erreur conçus explicitement.
- Au-delà d’environ 50 lignes, prévoir pagination ou virtualisation selon le besoin.

### Dialogues et notifications

- Fond assombri entre 40 et 60 %.
- Fermeture explicite, touche Échap et gestion correcte du focus.
- Les toasts confirment une action brève et disparaissent après 3 à 5 secondes sans voler le focus.
- Une erreur importante reste visible tant qu’elle n’est pas résolue.

## 10. Mouvement et retours d’interaction

Le mouvement doit expliquer une relation ou confirmer une action.

- Survol et focus : 150 à 180 ms.
- Changement d’état : 180 à 240 ms.
- Ouverture de panneau ou dialogue : 240 à 320 ms.
- Sortie environ 30 % plus rapide que l’entrée.
- Animer uniquement `transform` et `opacity` lorsque possible.
- Limiter chaque vue à un ou deux mouvements visibles simultanément.
- Les animations doivent être interruptibles et ne jamais bloquer une action.
- Aucun effet ne doit déplacer la mise en page ou provoquer un CLS.
- Respecter intégralement `prefers-reduced-motion`.

Exemples autorisés : léger éclaircissement d’une surface, apparition d’un panneau depuis son déclencheur, crossfade de données, progression animée une seule fois.

Exemples interdits : icônes qui rebondissent en boucle, parallax marqué, compteurs continuellement animés, cartes qui flottent en permanence.

## 11. Chargement, vide, erreur et données fraîches

Chaque composant alimenté par une API doit posséder quatre états conçus :

1. chargement ;
2. données disponibles ;
3. aucune donnée ;
4. erreur.

Règles :

- au-delà de 300 ms, afficher un skeleton aux dimensions finales ;
- réserver l’espace avant le chargement pour éviter les sauts ;
- conserver les dernières données connues si une actualisation échoue, avec un indicateur « Données potentiellement obsolètes » ;
- afficher l’heure de dernière mise à jour près des informations temps réel ;
- fournir une action « Réessayer » quand elle peut résoudre le problème ;
- ne jamais remplacer tout l’écran par un spinner si seule une carte se recharge.

## 12. Accessibilité obligatoire

- Navigation complète au clavier dans un ordre logique.
- Lien « Aller au contenu » conservé.
- Focus visible de 2 à 3 px, jamais supprimé.
- Hiérarchie de titres séquentielle.
- Icônes seules nommées avec un libellé accessible.
- États sélectionné, étendu, occupé et désactivé exposés sémantiquement.
- Zones interactives d’au moins 44 × 44 px.
- Contraste vérifié et sens jamais transmis par la couleur seule.
- Annonces `aria-live` réservées aux changements utiles ; ne pas annoncer chaque rafraîchissement automatique.
- Les tableaux conservent leurs en-têtes et relations sémantiques.
- Les dialogues piègent le focus et le rendent au contrôle d’origine.
- Zoom navigateur autorisé et contenu utilisable à 200 %.
- Interface utilisable avec mouvement réduit.

## 13. Performance perçue et réelle

- Précharger uniquement la police et les ressources critiques.
- Utiliser `font-display: swap` ou `optional` avec un fallback métriquement proche.
- Servir les images en WebP ou AVIF, avec dimensions et variantes responsives.
- Charger paresseusement les contenus hors écran.
- Réserver la taille des graphiques et jaquettes.
- Éviter les flous lourds sur de grandes surfaces et les animations coûteuses.
- Maintenir les interactions sous 100 ms et viser 60 fps pendant les transitions.
- Actualiser uniquement les composants dont les données ont changé.
- Ne pas ajouter de bibliothèque lourde pour un effet réalisable simplement.

## 14. Ordre d’implémentation recommandé

1. Inventorier les fonctions et états actuels sans en supprimer.
2. Créer les tokens partagés : couleurs, typographie, espacements, rayons, ombres, mouvement et z-index.
3. Construire le shell responsive : sidebar, en-tête mobile et zone principale.
4. Construire les composants communs et tous leurs états.
5. Refaire la Vue d’ensemble pour valider la direction visuelle.
6. Adapter Torrents, puis Prowlarr, en préservant les comportements existants.
7. Harmoniser Stockage, Médias, Santé et Activité.
8. Ajouter les micro-interactions et skeletons après stabilisation des layouts.
9. Vérifier clavier, lecteur d’écran, contraste, zoom et mouvement réduit.
10. Réaliser une passe finale de cohérence visuelle sur tous les écrans.

Ne pas implémenter écran par écran avec des styles isolés. Le shell, les tokens et les composants partagés doivent précéder les pages.

## 15. Définition de « qualité premium »

La refonte est terminée uniquement si :

- l’état global est compris en moins de trois secondes ;
- chaque écran possède un point focal évident ;
- aucune fonction actuelle n’a disparu ;
- les composants identiques ont exactement les mêmes dimensions et états ;
- aucune couleur, ombre ou animation arbitraire n’apparaît dans une page isolée ;
- aucun texte important n’est tronqué sans moyen d’accéder à sa valeur complète ;
- les quatre états de données sont traités partout ;
- la navigation clavier et le focus sont complets ;
- les contrastes respectent WCAG AA ;
- l’interface reste utilisable à 375 px et à 200 % de zoom ;
- `prefers-reduced-motion` est respecté ;
- aucune animation ne provoque de déplacement de layout ;
- les vues principales restent rapides avec des listes réalistes ;
- le résultat final paraît calme, précis et cohérent plutôt que simplement spectaculaire.

## 16. Instruction prête à transmettre à un développeur ou à un agent

> Reconcevoir l’ensemble du Dashboard selon `DESIGN_INSTRUCTIONS.md`, en préservant toutes les fonctions et les contrats API existants. Commencer par auditer les comportements actuels, puis créer un design system partagé et le shell responsive avant de modifier les pages. Utiliser le mockup comme direction visuelle, pas comme spécification pixel-perfect. Ne pas copier Apple : viser le même niveau de soin avec une identité originale sombre, indigo et orientée centre de contrôle. Implémenter et vérifier tous les états interactifs, de chargement, de données vides et d’erreur. Tester à 375, 768, 1024 et 1440 px, au clavier, à 200 % de zoom et avec `prefers-reduced-motion`. Ne considérer le travail terminé qu’après validation de la checklist de la section 15.
