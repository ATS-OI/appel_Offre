# Récapitulatif — Étape 4 : mots-clés/acheteurs en base + site d'entraînement

## Schéma SQL (`schema.sql`)

Trois nouvelles tables, à exécuter dans le SQL Editor Supabase (RLS ouverte,
cohérent avec les choix déjà faits en étape 2/3 — "tout le monde" pour
l'instant) :

- **`mots_cles`** (`mot` unique) — mots-clés recherchés dans l'objet des
  avis. Amorcée avec la liste étendue (rénovation, construction, école...
  jusqu'à restructuration, salle de bain, saniclip, plan vasque, etc.).
- **`acheteurs_suivis`** (`nom` unique) — acheteurs/promoteurs à surveiller
  indépendamment des mots-clés objet. Amorcée avec SIDR, CBo, SHLMR, SEDRE,
  SPAG, ICADE, OPALE, JWH (orthographe vérifiée en direct sur BOAMP/TED —
  voir le plan de cette étape pour le détail des vérifications).
- **`raisons`** (`appel_offre_id` → `appels_offres.id`, `ON DELETE CASCADE`)
  — historique des commentaires associés à une décision. Un avis peut
  accumuler plusieurs lignes `raisons` dans le temps (si la décision change).
  Supprimer l'avis supprime automatiquement ses raisons (cascade).

## Logique de requête combinée

La recherche BOAMP/TED est maintenant :

```
((mots-clés objet OR) OR (acheteurs suivis OR)) AND (départements OR) AND (deadline)
```

- BOAMP : `(objet like "%mot%" or ...) or (nomacheteur like "%SIDR%" or ...)`.
- TED : `(FT~"mot" OR ...) OR (FT~"SIDR" OR ...)` — `FT~` est une recherche
  plein texte qui couvre déjà tout le contenu de la notice, y compris le nom
  de l'acheteur (pas de champ dédié filtrable pour ça côté TED).

Un avis matche donc soit parce que son objet contient un mot-clé, soit parce
que son acheteur fait partie de la liste suivie — même si l'objet ne
contient aucun mot-clé (ex. un avis SIDR qui ne dirait pas "rénovation" en
toutes lettres).

⚠️ Le **score**, lui, ne compte toujours que les mots-clés objet (voir
`scoring.py`, inchangé depuis l'étape 3) — le filtre acheteur sert à élargir
la collecte, pas à évaluer la pertinence.

## Pourquoi mots-clés/acheteurs sont passés en base

Avant (étape 3) : `KEYWORDS` était une constante Python locale à qui lançait
le script — deux utilisateurs pouvaient donc avoir des listes différentes,
et le score n'aurait pas été calculé pareil pour tout le monde. Maintenant,
`listes_partagees.py` lit/écrit directement les tables Supabase : ajouter ou
retirer un mot-clé depuis le site (`app.py`) change immédiatement la même
liste pour tous les utilisateurs et pour tous les prochains calculs de score.

## Site (`app.py`)

- **Panneau "Mots-clés et acheteurs suivis"** (repliable) : chaque valeur
  s'affiche comme un bouton `✕ valeur` — cliquer dessus la supprime de la
  base immédiatement. Un champ + bouton "➕ Ajouter" en dessous de chaque
  liste.
- **"🔍 Lancer la recherche"** : pipeline complet (étape 2+3+4), mots-clés et
  acheteurs lus depuis Supabase à chaque exécution.
- **"🔄 Recalculer les scores"** : relit les mots-clés actuels et recalcule
  `score` pour tous les avis déjà en base, sans réinterroger BOAMP/TED —
  utile juste après avoir modifié la liste de mots-clés, pour voir l'effet
  immédiatement.
- **Fiche unique ("swipe")** : parmi les avis `decision = 'n/A'`, celui avec
  le score le plus élevé est affiché. Boutons "✅ Accepter" / "❌ Rejeter" /
  "⏳ Pour l'instant", avec une zone de commentaire optionnelle juste
  au-dessus. Au clic : la `decision` est mise à jour sur `appels_offres`,
  ET une ligne est ajoutée dans `raisons` (avec le commentaire, même vide) —
  puis la fiche suivante s'affiche automatiquement (elle n'est plus `n/A`).

## Fichiers du dossier

- `schema.sql` — SQL des 3 tables (à exécuter manuellement dans Supabase).
- `recupDataBaseOfficial.py` — copie étape 3 + paramètre `acheteurs` optionnel.
- `listes_partagees.py` — accès générique aux tables `mots_cles`/`acheteurs_suivis`.
- `scoring.py` — inchangé depuis l'étape 3.
- `InsertIntoDataBase.py` — pipeline complet, mots-clés/acheteurs chargés depuis Supabase,
  + fonction `recalculer_scores()`.
- `app.py` — le site.
- `.env` / `.env.example` / `requirements.txt` — copiés depuis l'étape 3.
