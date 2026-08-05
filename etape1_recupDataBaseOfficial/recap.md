# Récapitulatif des champs — BOAMP et TED (JOUE)

Ce document liste les champs bruts renvoyés par les deux API interrogées par
[`main.py`](main.py), leur nom exact et ce qu'ils représentent. Toutes les
requêtes ci-dessous ont été testées en direct contre les API réelles le
2026-08-05.

---

## 1. BOAMP (Bulletin officiel des annonces de marchés publics)

- **Éditeur** : DILA (Direction de l'information légale et administrative)
- **Endpoint utilisé** : `GET https://boamp-datadila.opendatasoft.com/api/v2/catalog/datasets/boamp/records`
- **Format de requête** : ODSQL (`where=...`), voir [documentation Opendatasoft](https://help.opendatasoft.com/apis/ods-search-v2/)
- **Structure de la réponse** : `{"records": [{"record": {"fields": {...}}}]}` — le script lit `record.fields`

### Champs du dataset (41 champs au total, source de vérité : le schéma live de l'API)

| Champ | Type | Description |
|---|---|---|
| `idweb` | text | Identifiant de l'avis tel qu'affiché sur boamp.fr (ex. `16-10169`) |
| `id` | text | Identifiant interne BOAMP |
| `contractfolderid` | text | Identifiant du dossier de consultation (souvent vide) |
| **`objet`** | text | **Objet / titre de l'annonce** — champ utilisé pour le filtre mots-clés (`objet like "%mot%"`) |
| `filename` | text | Nom du fichier XML source de l'avis |
| `famille` | text | Famille de l'avis (ex. `JOUE` si l'avis est aussi publié au niveau européen) |
| **`code_departement`** | text (liste) | **Département(s) concerné(s) par l'avis** — champ utilisé pour le filtre provenance (974/976). C'est une liste (ex. `["974", "976"]` pour un marché à lots multi-territoires) ; en pratique il mélange département de l'acheteur et département(s) d'exécution selon les avis. |
| `code_departement_prestation` | text | Département du **lieu d'exécution** au sens strict. ⚠️ Champ présent dans le schéma mais **quasiment jamais renseigné sur les avis récents** (vérifié en direct le 2026-08-05 : 0 résultat sur les avis 2026, alors que `code_departement` en compte des milliers). Non utilisé par le script pour cette raison — gardé ici à titre informatif au cas où la DILA le repeuplerait un jour. |
| `famille_libelle` | text | Libellé de la famille (ex. "Marchés européens") |
| `dateparution` | date | Date de publication de l'avis |
| `datefindiffusion` | date | Date de fin de diffusion de l'avis |
| **`datelimitereponse`** | datetime | **Date et heure limites de réception des offres** — champ utilisé pour le filtre "encore ouvert" |
| `nomacheteur` | text | Nom de l'organisme acheteur (ex. "Commune du Tampon") |
| `titulaire` | text | Nom du titulaire retenu (rempli uniquement pour les avis d'attribution) |
| `perimetre` | text | Périmètre réglementaire (ex. `DIRECTIVE-18`) |
| `type_procedure` | text | Type de procédure (ex. `OUVERT`) |
| `soustype_procedure` | text | Sous-type de procédure |
| `procedure_libelle` | text | Libellé de la procédure (ex. "Procédure Ouverte") |
| `procedure_categorise` | text | Procédure catégorisée |
| `nature` | text | Nature de l'avis (ex. `APPEL_OFFRE`) |
| `sousnature` | text | Sous-nature de l'avis |
| `nature_libelle` | text | Libellé de la nature (ex. "Avis de marché") |
| `sousnature_libelle` | text | Libellé de la sous-nature |
| `nature_categorise` | text | Nature catégorisée |
| `nature_categorise_libelle` | text | Libellé de la nature catégorisée |
| `criteres` | text | Critères d'attribution (souvent vide dans les avis simples) |
| `marche_public_simplifie` | text | Indicateur "marché public simplifié" |
| `marche_public_simplifie_label` | text | Libellé associé |
| `etat` | text | État de l'avis (ex. `INITIAL`, rectificatif...) |
| `descripteur_code` | text (liste) | Codes des mots-clés/descripteurs officiels de l'avis |
| `dc` | text (liste) | Alias des codes descripteurs |
| `descripteur_libelle` | text (liste) | Libellés des descripteurs (ex. "Bardage", "Démolition", "Gros oeuvre") |
| `type_marche` | text | Type de marché (travaux / fournitures / services) |
| `type_marche_facette` | text | Type de marché en version "facette" pour le filtrage |
| `type_avis` | text (liste) | Codes du type d'avis |
| `annonce_lie` | text | Référence vers une annonce liée |
| `annonces_anterieures` | text | Référence vers une annonce antérieure (cas des rectificatifs) |
| `source_schema` | text | Version du schéma XML source (ex. `Boamp_v230.xsd`) |
| `gestion` | text | Bloc JSON brut de métadonnées de gestion interne |
| `donnees` | text | Bloc JSON brut des données complètes de l'avis |
| **`url_avis`** | text | **URL publique de l'avis sur boamp.fr** |

### Filtre construit par le script

```
(objet like "%rénovation%" or objet like "%construction%" or ... )
and (code_departement="974" or code_departement="976")
and datelimitereponse > date'AAAA-MM-JJ'
```

---

## 2. TED / JOUE (Tenders Electronic Daily — marchés européens)

- **Éditeur** : Office des publications de l'Union européenne
- **Endpoint utilisé** : `POST https://api.ted.europa.eu/v3/notices/search` (public, sans clé d'API)
- **Format de requête** : "expert query" (langage propre à TED) dans le champ JSON `query`, + liste des champs à renvoyer dans `fields`
- **Format de réponse** : `{"notices": [...], "totalNoticeCount": N}`
- **Nomenclature des champs** : eForms (format de notice de marché public standardisé de l'UE), noms en kebab-case

> ⚠️ La documentation Swagger officielle (`api.ted.europa.eu/swagger-ui`) est
> une application JavaScript non lisible directement par un simple outil de
> récupération de page. Les noms de champs ci-dessous ont donc été
> **vérifiés par appels réels à l'API** (essais/erreurs), pas uniquement lus
> dans une documentation statique. Si l'API TED change ses noms de champs à
> l'avenir, le script affichera le message d'erreur brut renvoyé par l'API
> (très explicite, ex. `Unknown search field 'xxx'`), ce qui permet de
> corriger rapidement `construire_requete_ted()` dans `main.py`.

### Champs demandés par le script (`TED_FIELDS`) et leur contenu

| Champ | Description |
|---|---|
| `publication-number` | Numéro de publication de la notice (ex. `401901-2026`) — identifiant unique TED |
| `notice-title` | Titre de la notice, **objet multilingue** (`{"fra": [...], "eng": [...], ...}`) — le script prend la version française en priorité |
| `buyer-name` | Nom de l'acheteur public, objet multilingue similaire |
| `buyer-country` | Code pays (ISO3) de l'acheteur |
| `place-of-performance` | Liste de codes géographiques du lieu d'exécution : mélange de codes NUTS précis (ex. `FRY40` = La Réunion, `FRY50` = Mayotte) et du code pays générique (`FRA`) |
| `deadline-receipt-tender-date-lot` | Date(s) limite(s) de réception des offres, une par lot ; le script prend la première valeur |
| `links` | URLs de la notice dans toutes les langues (HTML, PDF, XML) ; le script utilise `links.html.FRA` |

### Syntaxe de la requête "expert query" utilisée

- **Texte libre (mots-clés)** : `FT~"mot"` → recherche plein texte ; combinable en OU : `FT~"mot1" OR FT~"mot2"`
- **Lieu d'exécution (géographie)** : `place-of-performance=<code>` — accepte soit un code pays ISO3 (`FRA`), soit un code NUTS précis. **Important** : les départements d'outre-mer utilisent la nomenclature **NUTS révision 2021**, pas les anciens codes NUTS3 2016 :
  - `974` (La Réunion) → **`FRY4`** (matche les valeurs détaillées type `FRY40`)
  - `976` (Mayotte) → **`FRY5`** (matche les valeurs détaillées type `FRY50`)
  - (pour référence : Guadeloupe = `FRY1`, Martinique = `FRY2`, Guyane = `FRY3`)
- **Date limite de réponse** : `deadline-receipt-tender-date-lot>=today()` pour ne garder que les avis encore ouverts (ou `>=YYYYMMDD` pour une date fixe)
- **Combinaison ET/OU** : opérateurs `AND` / `OR` avec parenthèses, comme en SQL

### Filtre construit par le script

```
(FT~"rénovation" OR FT~"construction" OR ... )
AND (place-of-performance=FRY4 OR place-of-performance=FRY5)
AND deadline-receipt-tender-date-lot>=today()
```

### Exemple réel obtenu pendant les tests

Avec ce filtre exact, l'API a par exemple renvoyé un avis du **Rectorat de
Mayotte** avec une date limite au 2026-08-14 (encore ouverte), et un avis de
la **Direction d'Infrastructure de la Défense de Saint-Denis** portant sur
des "travaux d'entretien et de rénovation... à la Réunion (974) et à Mayotte
(976)" — confirmant que le filtre géographique et le filtre mots-clés
fonctionnent comme attendu.

---

## 3. Notes générales

- **Logique de filtre** (identique sur les deux sources) : OU entre les
  mots-clés, ET entre les différents champs (mots-clés / provenance / date).
- **Codes NUTS 2021 pour les DOM** (utile si vous étendez `DEPARTEMENTS`) :
  Guadeloupe = `FRY1`, Martinique = `FRY2`, Guyane = `FRY3`, La Réunion =
  `FRY4`, Mayotte = `FRY5`.
- **BOAMP vs TED** : BOAMP couvre tous les marchés publics français quel que
  soit leur montant ; TED ne couvre que les marchés dont le montant dépasse
  les seuils de publicité européenne. Les deux sources se complètent donc
  plutôt qu'elles ne se recoupent, d'où l'intérêt d'interroger les deux.
- Pour aller plus loin sur un avis TED précis, le champ `publication-number`
  permet de reconstruire son URL : `https://ted.europa.eu/fr/notice/-/detail/<publication-number>`.
