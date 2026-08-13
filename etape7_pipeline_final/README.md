# Appels d'offres — La Réunion / Mayotte

Un site interne qui récupère automatiquement les appels d'offres publics
publiés sur 4 plateformes (BOAMP, TED/JOUE, AWSolutions, PLACE) pour La
Réunion et Mayotte, les présente un par un ("à la Tinder") pour que l'équipe
les marque "intéressant" ou "pas intéressant", et apprend progressivement à
mettre en avant les avis qui ressemblent à ceux déjà marqués intéressants.

Pour comprendre comment le code est organisé, voir [structure.md](structure.md).

## Ce que fait le site

Le site est organisé en 3 onglets :

1. **🎯 Trier** : "🔍 Lancer la recherche" (barre latérale) supprime d'abord
   les avis déjà en base dont la date limite de réponse est dépassée
   (inutiles une fois qu'on ne peut plus y répondre), puis interroge les 4
   sources pour les départements suivis (974/976 par défaut, voir
   `config.json`) — uniquement les avis encore ouverts — et garde ceux dont
   l'objet, un lot, ou l'acheteur correspond aux mots-clés/acheteurs suivis
   (préremplis avec le périmètre connu de l'équipe, voir `schema.sql`, gérés
   depuis l'onglet "🔑 Mots-clés"). Chaque personne de l'équipe voit ensuite
   une fiche à la fois (tirée au hasard parmi les avis qu'elle n'a pas
   encore traités), avec les mots-clés qui l'ont fait remonter affichés
   dessus, et clique "👍 Intéressant" ou "👎 Pas intéressant", avec un
   commentaire optionnel. Chaque personne a sa propre file : ce qu'Alice
   trie n'affecte pas la file de Bob.
2. **📋 Historique** : tous les avis en base, avec VOTRE décision indiquée
   par une couleur (vert = intéressant, rouge = pas intéressant, gris = pas
   encore trié) — filtrable pour ne voir que les intéressants ou que les
   pas intéressants.
3. **🔑 Mots-clés** : gestion des 3 listes partagées (mots-clés objet,
   mots-clés lots, acheteurs suivis) qui pilotent le périmètre de recherche.

Le **score** (sur 100, affiché sur chaque fiche) est au début une estimation
simple (mots-clés + délai restant). Une fois assez de décisions enregistrées
(30 par défaut, tous utilisateurs confondus), il devient la proportion
d'avis "intéressants" parmi les décisions passées qui ressemblent le plus
(au sens sémantique) à l'avis en cours — voir `scoring.py`.

Une panne d'une des 4 sources (site indisponible, identifiants manquants...)
n'empêche jamais les 3 autres de fonctionner : un message rouge s'affiche en
haut du site pour prévenir, avec le fichier à regarder en cas de doute.

## Lancer le site en local

Prérequis : Python 3.11+, un projet Supabase.

```bash
cd etape7_pipeline_final
pip install -r requirements.txt
playwright install chromium   # une seule fois, nécessaire pour la source AWS

cp .env.example .env          # puis remplir SUPABASE_URL / SUPABASE_KEY
                               # (EMAIL_AWS / MOT_DE_PASSE_AWS optionnels)

streamlit run app.py
```

### Base de données

Sur un projet Supabase **vidé** (voir plus bas si vous partez d'une base
existante), ouvrez l'éditeur SQL et exécutez tout le contenu de
[`schema.sql`](schema.sql) une seule fois. Il crée toutes les tables (déjà
préremplies avec le périmètre de recherche connu de l'équipe : mots-clés,
mots-clés lots, acheteurs suivis), la fonction de recherche par similarité,
et les permissions nécessaires. Exécutez ensuite
[`schema2.sql`](schema2.sql) (migration additive, sans risque à rejouer) :
elle ajoute la colonne qui garde trace des mots-clés ayant fait remonter
chaque avis (affichée sur la fiche, onglet "🎯 Trier").

⚠️ Si vous avez une base Supabase existante d'une version précédente du
projet (`etape5_scoring_ia` ou antérieure) : supprimez toutes les tables
avant d'exécuter `schema.sql` (ex. dans l'éditeur SQL Supabase : `drop
schema public cascade; create schema public;`), le schéma a changé
(plus de modèles A/B, une seule table de features).

## Déployer pour toute l'équipe (Streamlit Community Cloud)

La façon la plus simple de rendre le site accessible à tout le monde sans
gérer de serveur soi-même :

1. Poussez ce dossier sur un dépôt GitHub (ex. `github.com/ATS-OI/appel_Offre`).
2. Allez sur [share.streamlit.io](https://share.streamlit.io), connectez-vous
   avec GitHub.
3. "New app" → choisissez le dépôt, la branche, et comme fichier principal :
   `etape7_pipeline_final/app.py`.
4. Dans "Advanced settings" → "Secrets", collez le contenu de votre `.env`
   au format TOML :
   ```toml
   SUPABASE_URL = "https://xxxxx.supabase.co"
   SUPABASE_KEY = "..."
   EMAIL_AWS = "..."
   MOT_DE_PASSE_AWS = "..."
   ```
5. Déployez. Streamlit Cloud installe automatiquement `requirements.txt`
   (dépendances Python) et `packages.txt` (bibliothèques système nécessaires
   à Chromium headless, pour la source AWS) — le téléchargement du
   navigateur lui-même (`playwright install chromium`) se déclenche tout
   seul au premier lancement de la source AWS (voir
   `sources/aws_solutions.py::_assurer_navigateur_installe`), Streamlit
   Cloud n'exécutant pas cette étape automatiquement contrairement à
   `pip install`. `packages.txt` installe le paquet `chromium` du système
   (pas utilisé directement — Playwright garde son propre navigateur
   téléchargé) uniquement pour récupérer, en une fois, toutes les
   bibliothèques partagées dont ce navigateur a besoin pour démarrer :
   plus robuste que lister les bibliothèques une par une (leurs noms exacts
   changent d'une version de Debian à l'autre, et si un seul nom est faux,
   `apt-get install` échoue pour toute la liste). Si la source AWS échoue
   quand même, le reste du site continue de fonctionner normalement (voir
   "panne d'une source" ci-dessus) — dans ce
   cas, hébergez plutôt via Docker/VM (option ci-dessous), qui n'a pas cette
   limite.
6. Le lien fourni par Streamlit Cloud (ex.
   `https://appel-offre-ats-oi.streamlit.app`) est celui à partager à toute
   l'équipe — un simple favori dans le navigateur suffit, aucune
   installation nécessaire côté utilisateur.

## Alternative : Docker / VM (si Playwright/AWS doit fonctionner en ligne)

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt && playwright install --with-deps chromium
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
```

Hébergez l'image sur n'importe quelle VM (ou service de conteneurs) que
l'équipe peut atteindre en réseau interne, avec les mêmes variables d'env
que `.env`.

## Limites connues

- **PLACE** : scraping HTML (pas d'API publique) — ne remonte actuellement
  ni date limite ni lien direct par avis (voir `sources/place.py`), et la
  recherche est câblée en dur sur La Réunion + Mayotte. Un changement du
  site casse ce scraper ; le reste du pipeline continue (encadré rouge).
- **AWS** : nécessite un compte AWSolutions valide (`EMAIL_AWS`/
  `MOT_DE_PASSE_AWS`) et un navigateur headless (Playwright) — sans ça,
  cette source échoue proprement, le reste continue.
- **Rapprochement des doublons** (un même marché republié/rectifié, y
  compris entre deux sources différentes) est une heuristique (acheteur +
  similarité de texte), pas un identifiant officiel commun — en cas de
  doute, vérifier via les liens fournis.
- **Score** : le KNN compare aux décisions déjà prises ; avec très peu de
  décisions dans une direction (ex. presque uniquement des rejets), le
  score peut rester peu informatif au-delà de la proportion générale — plus
  l'équipe trie, plus il devient pertinent.
