# Récapitulatif — Étape 3 : scoring + site de décision

## Scoring (`scoring.py`)

Heuristique provisoire (pas encore d'IA), calculée à **chaque insertion/mise à jour** d'un avis (donc recalculée à chaque clic sur "Lancer la recherche", puisque le nombre de jours restants change tous les jours) :

```
score = points_mots_cles + points_delai        (sur 100)

points_mots_cles = min(nb_mots_cles_trouves, 4) / 4 * 60
points_delai     = min(jours_restants, 45) / 45 * 40
```

- `nb_mots_cles_trouves` : nombre de mots-clés de `KEYWORDS` (dans `InsertIntoDataBase.py`) retrouvés dans le champ `objet` de l'avis (comparaison insensible aux accents/casse).
- `jours_restants` : nombre de jours entre aujourd'hui et `date_limite_reponse`. Pour l'instant, **plus il y a de temps, mieux c'est** (permet de préparer une meilleure offre) — critère volontairement simple, à affiner plus tard.
- Les seuils (`4` mots-clés, `45` jours) et les poids (`60`/`40`) sont des constantes en haut de `scoring.py`, ajustables sans toucher au reste du code.
- Remplacer par une vraie IA de scoring plus tard = ne modifier que la fonction `calculer_score()`, tout le reste du pipeline reste inchangé.

## Décision (`app.py`)

La colonne `decision` n'est **jamais** modifiée par le pipeline de récupération (`InsertIntoDataBase.py` omet volontairement ce champ de l'upsert, pour ne jamais écraser un choix déjà fait). Elle n'est modifiée que depuis le site, via les boutons :

| Bouton | Valeur stockée |
|---|---|
| ✅ Accepter | `accepted` |
| ❌ Rejeter | `rejected` |
| ⏳ Rejeter (pour l'instant) | `rejected (for now)` |
| ↩️ Réinitialiser | `n/A` (valeur par défaut) |

## Site (`app.py`, Streamlit)

Choix technique : **Streamlit** plutôt qu'un framework web classique (Flask/Django) — c'est le plus rapide à mettre en place pour un tableau de bord interne avec liste + boutons + connexion directe à Supabase, sans avoir à écrire de HTML/JS. Si vous préférez un vrai site (multi-pages, design personnalisé, déploiement public...), on peut migrer vers Flask/Next.js plus tard — le pipeline (`InsertIntoDataBase.py`) et Supabase restent identiques dans ce cas, seule la couche d'affichage change.

Lancement :

```bash
pip install -r requirements.txt
streamlit run app.py
```

Fonctionnalités :
- **Bouton "🔍 Lancer la recherche"** : exécute `lancer_recherche_et_insertion()` (récupération BOAMP/TED + scoring + upsert Supabase), avec indicateur de chargement (peut prendre 20-60 secondes).
- **Liste des avis**, triée par date limite de réponse croissante, avec filtre par décision.
- **Boutons de décision** par avis (le bouton correspondant à la décision actuelle est désactivé).

## Configuration Supabase requise (déjà en place depuis l'étape 2)

- Contrainte `UNIQUE (identifiants)` sur la table `appels_offres`.
- Policies RLS ouvertes (lecture/écriture publiques) — voir étape 2 pour le SQL. À resserrer avant toute mise en production réelle.
