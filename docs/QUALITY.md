# Critères de qualité

## Contrôles automatisés

Avant chaque livraison :

```bash
make check
make audit
docker compose config --quiet
```

La CI doit être verte et les bundles générés ne doivent présenter aucune
différence après `make build`.

## Parcours fonctionnels

- Torrent : rechercher, filtrer, trier, sélectionner, ajouter, suspendre,
  reprendre et ouvrir les détails.
- Prowlarr : rechercher, tester un indexeur, activer/désactiver et récupérer une
  release.
- Cloud : naviguer, rechercher, trier, téléverser, télécharger, renommer,
  supprimer et gérer un lien de partage.
- États : chargement, vide, erreur réseau, indisponibilité d’une dépendance,
  succès et action partiellement échouée.

## Matrice responsive

Contrôler chaque vue à `1600`, `1440`, `1024`, `768` et `375` px :

- aucune barre de défilement horizontale involontaire ;
- texte et actions non tronqués ;
- tableaux transformés ou défilables sans perte d’information ;
- cibles interactives d’au moins 44 × 44 px ;
- dialogues entièrement accessibles sans sortir de l’écran.

## Accessibilité

- navigation complète au clavier et ordre de focus logique ;
- focus visible, restauré à la fermeture d’un dialogue ;
- un seul `h1`, titres séquentiels et libellés de formulaire explicites ;
- états annoncés par `aria-live` lorsqu’ils changent sans navigation ;
- `aria-current="page"` uniquement sur l’entrée active ;
- contraste WCAG AA et information jamais transmise par la couleur seule ;
- animations neutralisées avec `prefers-reduced-motion`.

## Sécurité frontend

- aucune donnée distante injectée avec `innerHTML` ;
- aucune URL interne, clé, cookie ou erreur brute affichée ;
- aucun script, gestionnaire ou style inline incompatible avec la CSP ;
- tout lien ouvrant un nouvel onglet utilise `noopener noreferrer` ;
- tout changement d’état passe par le client CSRF partagé.
