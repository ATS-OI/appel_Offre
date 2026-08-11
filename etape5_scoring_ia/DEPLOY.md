# Déploiement — rendre `app.py` accessible à toute l'équipe

Objectif : un **lien unique** (`https://....streamlit.app`), sans que
personne n'ait à installer Python, cloner le repo ou lancer une commande.
Le dépôt (`https://github.com/ATS-OI/appel_Offre`) est déjà sur GitHub,
donc la voie la plus simple et gratuite est **Streamlit Community Cloud**.

## Option recommandée : Streamlit Community Cloud (gratuit)

1. **Vérifier que le repo est à jour sur GitHub** (`git push`) — le dépôt
   doit être accessible depuis votre compte GitHub (public, ou privé si
   vous connectez ce compte à Streamlit Cloud).
2. Aller sur **https://share.streamlit.io** et se connecter avec le compte
   GitHub qui a accès au repo `ATS-OI/appel_Offre`.
3. **"New app"** →
   - Repository : `ATS-OI/appel_Offre`
   - Branch : `main`
   - Main file path : `etape5_scoring_ia/app.py`
4. **Secrets** (remplace le `.env` local, jamais commité — voir
   `.gitignore`) : dans les réglages de l'app → *Settings → Secrets*, coller :
   ```toml
   SUPABASE_URL = "https://xxxxxxxxxxxxxxxx.supabase.co"
   SUPABASE_KEY = "votre-cle-api-supabase"
   ```
   `app.py` lit actuellement ces valeurs via `os.environ` après
   `load_dotenv(...)` — Streamlit Cloud injecte automatiquement les secrets
   déclarés ici comme variables d'environnement au démarrage, donc **aucun
   changement de code n'est nécessaire**.
5. **Dépendances** : Streamlit Cloud installe automatiquement
   `etape5_scoring_ia/requirements.txt` s'il détecte le fichier à côté du
   script principal — c'est déjà le cas ici. Premier démarrage plus long
   (~2-5 min) le temps de télécharger `sentence-transformers` et le modèle
   `multilingual-e5-large` (~2 Go) ; les démarrages suivants sont plus
   rapides (mise en cache par la plateforme, cependant elle peut être
   réinitialisée après une longue période d'inactivité).
6. Cliquer **"Deploy"**. Une fois prêt, l'URL générée
   (`https://<nom-app>.streamlit.app`) est celle à partager à toute
   l'équipe — un simple lien, aucune installation requise côté employés.
7. Pour publier une mise à jour : `git push` sur `main` → l'app se
   redéploie automatiquement (redémarrage à chaud en general en quelques
   dizaines de secondes, sauf si `requirements.txt` a changé).

**Limites à connaître** (offre gratuite) :
- L'app se met en veille après une longue inactivité (~plusieurs jours
  sans visite) et redémarre au prochain accès (délai de quelques dizaines
  de secondes, normal).
- Ressources limitées (CPU/RAM partagés) — largement suffisant pour un
  usage interne de quelques dizaines de swipes/jour avec ce modèle
  d'embedding, mais à surveiller si l'équipe grandit beaucoup.
- Si le repo GitHub est privé, seuls les comptes autorisés dessus peuvent
  déployer/gérer l'app (les *utilisateurs* du lien final, eux, n'ont besoin
  d'aucun compte — l'app reste ouverte à qui a le lien, sauf si vous activez
  la restriction d'accès par e-mail dans les réglages Streamlit Cloud).

## Alternative : conteneur Docker sur un petit serveur/VM

Utile si vous préférez tout garder en interne (pas de dépendance à
Streamlit Cloud) ou si vous avez déjà un serveur/VM (OVH, Scaleway, VM
interne, etc.).

1. `etape5_scoring_ia/Dockerfile` (à créer) :
   ```dockerfile
   FROM python:3.11-slim
   WORKDIR /app
   COPY etape5_scoring_ia/requirements.txt .
   RUN pip install --no-cache-dir -r requirements.txt
   COPY etape5_scoring_ia/ .
   EXPOSE 8501
   CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
   ```
2. Construire et lancer (sur le serveur, avec les vraies variables
   d'environnement — ne pas copier le `.env` dans l'image) :
   ```bash
   docker build -t appel-offre-app -f etape5_scoring_ia/Dockerfile .
   docker run -d --restart unless-stopped -p 8501:8501 \
     -e SUPABASE_URL=https://xxxxxxxxxxxxxxxx.supabase.co \
     -e SUPABASE_KEY=votre-cle-api-supabase \
     --name appel-offre-app appel-offre-app
   ```
3. Rendre accessible à l'équipe :
   - Sur le même réseau d'entreprise (VPN/LAN) : `http://<ip-serveur>:8501`
     suffit, pas besoin de nom de domaine.
   - Accessible depuis l'extérieur : mettre un reverse proxy devant
     (nginx/Caddy) avec HTTPS (ex. Let's Encrypt via Caddy, configuration
     automatique) plutôt que d'exposer le port 8501 directement.
4. Mise à jour : `git pull` puis `docker build` + `docker restart` (ou un
   petit script/CI qui fait ça à chaque push sur `main`).

Cette option demande plus de maintenance (mises à jour de sécurité de l'OS,
monitoring, HTTPS à gérer soi-même) mais garde tout en interne — à choisir
seulement si Streamlit Cloud ne convient pas (données trop sensibles pour
un service tiers, besoin de performances garanties, etc.). Pour un usage
interne standard, **Streamlit Community Cloud reste le choix le plus
simple**.

## Dans tous les cas

- Les identifiants Supabase (`SUPABASE_URL`/`SUPABASE_KEY`) ne doivent
  **jamais** être commités dans le repo (`.env` est déjà dans
  `.gitignore`) — ils passent uniquement par les secrets de la plateforme
  de déploiement (Streamlit Cloud) ou des variables d'environnement au
  lancement du conteneur (Docker).
- La clé Supabase utilisée ici est celle du projet (anon/public key selon
  vos policies RLS actuelles — voir `schema.sql`, RLS ouvertes à tous pour
  l'instant) : elle donne accès en lecture/écriture à toute personne ayant
  le lien de l'app, ce qui est cohérent avec l'usage interne visé, mais à
  garder en tête si l'app devient accessible en dehors de l'équipe.
