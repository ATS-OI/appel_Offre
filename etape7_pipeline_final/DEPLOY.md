# Déploiement — toujours en ligne, pour toute l'équipe

Ce document couvre le cas "le site doit rester accessible en continu
pendant plusieurs semaines, sans se couper tout seul". Pour un simple test
rapide entre 2-3 personnes, voir la section "Streamlit Community Cloud" du
[README.md](README.md) — mais **pas pour un usage prolongé** (voir pourquoi
ci-dessous).

## Pourquoi pas Streamlit Community Cloud ici

- Une app gratuite se met en veille après ~7 jours sans visite et doit être
  réveillée manuellement — incompatible avec "reste en ligne tout seul".
- RAM limitée (1 Go) alors que le modèle d'embedding utilisé
  (`sentence-transformers`, e5-large) pèse à lui seul près de 2 Go une fois
  chargé — risque de plantage plutôt qu'un service stable.
- Bac à sable restreint : la source AWS a besoin d'un navigateur headless
  (Playwright), pas garanti de fonctionner de façon fiable sur ce type
  d'hébergement partagé.

## Recommandation : une petite VM avec Docker, toujours allumée

C'est la solution la plus robuste et la moins chère pour un usage interne
continu : une machine qui tourne 24h/24, un conteneur qui redémarre tout
seul en cas de crash ou de reboot de la machine.

### 1. Une VM Linux (Ubuntu 22.04 par exemple)

N'importe quel fournisseur convient (OVH, Scaleway, AWS Lightsail, Hetzner,
ou une VM déjà disponible en interne) — la config minimale suffit largement
pour cet usage : **2 vCPU / 4 Go de RAM** (le modèle d'embedding a besoin
d'un peu de marge). Coût typique : 5 à 10 €/mois chez la plupart des
fournisseurs européens.

### 2. Installer Docker sur la VM

```bash
curl -fsSL https://get.docker.com | sh
```

### 3. Récupérer le code sur la VM

```bash
git clone <votre-dépôt> appel_offre
cd appel_offre/etape7_pipeline_final
cp .env.example .env   # puis remplir SUPABASE_URL / SUPABASE_KEY / EMAIL_AWS / MOT_DE_PASSE_AWS
```

### 4. Construire l'image et lancer le conteneur

Le `Dockerfile` (déjà fourni dans le README, reproduit ici) :

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt && playwright install --with-deps chromium
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
```

```bash
docker build -t appel-offre .
docker run -d \
  --name appel-offre \
  --restart unless-stopped \
  --env-file .env \
  -p 8501:8501 \
  appel-offre
```

`--restart unless-stopped` : c'est la partie qui répond directement à votre
besoin — Docker relance automatiquement le conteneur s'il plante, ou après
un redémarrage de la VM (panne électrique, maintenance du fournisseur...).
Rien à surveiller manuellement.

### 5. Rendre le site accessible à l'équipe

Deux options selon vos contraintes :

- **Accès uniquement depuis le réseau de l'entreprise** (le plus simple et
  le plus sûr, pas d'authentification à gérer) : ouvrez le port 8501
  uniquement au réseau interne/VPN de l'entreprise dans le pare-feu de la
  VM, partagez `http://<ip-interne-de-la-vm>:8501`.
- **Accès depuis internet** (équipe distribuée, télétravail) : mettez un
  reverse proxy devant (ex. [Caddy](https://caddyfile.com), qui gère le
  HTTPS automatiquement) avec un nom de domaine, et ajoutez une
  authentification basique dessus (Streamlit lui-même n'a pas
  d'authentification intégrée — voir remarque plus bas) :

  ```
  # Caddyfile
  appels-offres.votre-domaine.fr {
      basic_auth {
          equipe $2a$14$...   # hash bcrypt du mot de passe, voir doc Caddy
      }
      reverse_proxy localhost:8501
  }
  ```

### 6. Mettre à jour le site plus tard

```bash
cd appel_offre && git pull
cd etape7_pipeline_final
docker build -t appel-offre .
docker stop appel-offre && docker rm appel-offre
docker run -d --name appel-offre --restart unless-stopped --env-file .env -p 8501:8501 appel-offre
```

## Alternative sans gérer de VM : Railway / Render (offre payante)

Si personne dans l'équipe ne veut administrer un serveur Linux : ces
plateformes déploient directement le `Dockerfile` depuis GitHub, en
quelques clics, et **leur offre payante (quelques dollars/mois) ne met pas
l'app en veille** (contrairement à Streamlit Community Cloud) — c'est le
compromis le plus simple entre "je ne gère rien" et "ça reste vraiment en
ligne en continu". Étapes similaires dans les deux cas :

1. Poussez le dépôt sur GitHub.
2. Connectez le dépôt sur [railway.app](https://railway.app) ou
   [render.com](https://render.com), pointez sur `etape7_pipeline_final/`
   (Dockerfile détecté automatiquement).
3. Renseignez les variables d'environnement (`SUPABASE_URL`, `SUPABASE_KEY`,
   `EMAIL_AWS`, `MOT_DE_PASSE_AWS`) dans les réglages du projet.
4. Choisissez un plan payant "always on" (pas le tier gratuit, qui a les
   mêmes limites de mise en veille que Streamlit Cloud).
5. Un lien HTTPS public est fourni automatiquement — à partager à l'équipe.

## Remarque : pas d'authentification intégrée

Dans les deux cas, le site lui-même n'a pas de mot de passe (identification
"légère" par nom saisi, voir README) — si le site est exposé sur internet
public, ajoutez une authentification devant (basic auth via le reverse
proxy comme ci-dessus, ou restreignez l'accès au VPN/réseau interne de
l'entreprise) plutôt que de le laisser entièrement ouvert.
