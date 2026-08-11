"""
app.py — Étape 5 (A/B test) : site "à la Tinder" avec 2 scorings IA (Streamlit)
================================================================================

Lancer avec :
    streamlit run app.py

Tout le contenu utile est visible sans scroller ni menu déroulant : gestion
des listes et actions globales dans une barre latérale toujours ouverte
(`st.sidebar`, pas d'expander), fiche "swipe" compacte au centre.

Fonctionnalités :
  - Identification légère (nom saisi, pas d'authentification) : chaque
    utilisateur a sa PROPRE file de tri, indépendante des autres — si Alice
    accepte un avis, il reste "à trier" pour Bob (voir recap.md).
  - Gestion des mots-clés (objet), mots-clés lots (détail des lots) et
    acheteurs suivis — pilotent le périmètre de recherche BOAMP/TED
    (filtrage côté client, voir recap.md), et contribuent aussi au scoring
    IA (`score_mots_cles`/`score_mots_cles_lots`, features parmi d'autres).
  - "🔍 Lancer la recherche" : pipeline complet (récupération + scoring IA
    des 2 modèles + insertion).
  - "🔄 Recalculer les scores" : recalcule les 2 scores de tous les avis en
    base avec les modèles actuels, sans réinterroger BOAMP/TED.
  - Une fiche à la fois parmi les avis que CET utilisateur n'a pas encore
    swipés, tirée **au hasard** (pas triée par score — biaiserait
    l'entraînement, voir recap.md), affichant les deux scores (modèle A /
    modèle B) côte à côte pour comparaison. Décision + commentaire optionnel
    → alimentent `raisons` (historique métier) ET `swipes` + les deux
    modèles partagés (`pipeline_scoring.update_model`).
"""

from __future__ import annotations

import os
import random
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from supabase import Client, create_client

from InsertIntoDataBase import lancer_recherche_et_insertion
from listes_partagees import (
    ajouter_acheteur_suivi,
    ajouter_mot_cle,
    ajouter_mot_cle_lot,
    charger_acheteurs_suivis,
    charger_mots_cles,
    charger_mots_cles_lots,
    retirer_acheteur_suivi,
    retirer_mot_cle,
    retirer_mot_cle_lot,
)
from modele_preference import SEUIL_COLD_START, charger_modele, poids_actuels
from pipeline_scoring import recalculer_tous_les_scores, update_model

NOM_TABLE = "appels_offres"
NOM_TABLE_SWIPES = "swipes"

DECISIONS: dict[str, str] = {
    "✅ Accepter": "accepted",
    "❌ Rejeter": "rejected",
}


@st.cache_resource
def get_client() -> Client:
    load_dotenv(Path(__file__).parent / ".env")
    url = os.environ.get("SUPABASE_URL")
    cle = os.environ.get("SUPABASE_KEY")
    if not url or not cle:
        st.error(
            "SUPABASE_URL et/ou SUPABASE_KEY manquants. "
            "Copiez .env.example vers .env (dans ce dossier) et remplissez vos identifiants Supabase."
        )
        st.stop()
    return create_client(url, cle)


def _ids_deja_swipes(client: Client, user_id: str) -> set[str]:
    """Avis déjà swipés par CET utilisateur — indépendant des autres (voir
    recap.md). `appels_offres.decision` n'est plus utilisé pour filtrer la
    file : c'est désormais `swipes.user_id` qui fait foi, par utilisateur.
    """
    reponse = client.table(NOM_TABLE_SWIPES).select("appel_offre_id").eq("user_id", user_id).execute()
    return {ligne["appel_offre_id"] for ligne in reponse.data} # type: ignore


def charger_offres_a_trier(client: Client, user_id: str) -> list[dict]:
    """Avis que CET utilisateur n'a pas encore swipés."""
    deja_swipes = _ids_deja_swipes(client, user_id)
    toutes_les_offres = client.table(NOM_TABLE).select("*").execute().data
    return [o for o in toutes_les_offres if o["id"] not in deja_swipes] # type: ignore


def charger_offre_suivante(client: Client, user_id: str) -> dict | None:
    """Un avis non encore swipé par CET utilisateur, tiré AU HASARD (pas par
    score — voir recap.md : trier par score biaise l'entraînement vers les
    avis déjà "évidents", au détriment des exemples informatifs pour le modèle).
    """
    offres = charger_offres_a_trier(client, user_id)
    return random.choice(offres) if offres else None


def compter_a_trier(client: Client, user_id: str) -> int:
    return len(charger_offres_a_trier(client, user_id))


def afficher_poids(client: Client, nom_modele: str) -> None:
    etat = charger_modele(client, nom_modele)
    if etat["nb_swipes_vus"] < SEUIL_COLD_START:
        st.write(f"Modèle {nom_modele} : cold start ({etat['nb_swipes_vus']}/{SEUIL_COLD_START}).")
        return
    st.write(f"Modèle {nom_modele} : appris ({etat['nb_swipes_vus']} swipes).")
    poids = {k: v for k, v in poids_actuels(etat["modele"]).items() if not k.startswith("emb_")}
    if poids:
        poids_tries = dict(sorted(poids.items(), key=lambda kv: abs(kv[1]), reverse=True)[:6])
        st.bar_chart(poids_tries)


# =====================================================================
# Page
# =====================================================================

st.set_page_config(page_title="A/B test scoring IA — BOAMP / TED", layout="wide")
client = get_client()

# --- Identification légère (pas d'authentification) : chaque nom saisi a sa
# propre file de tri, indépendante des autres — voir recap.md.
if "user_id" not in st.session_state:
    st.session_state["user_id"] = "anonyme"

# --- Barre latérale : listes partagées + actions + explicabilité --------
with st.sidebar:
    st.header("⚙️ Réglages")

    st.text_input(
        "👤 Votre nom",
        key="user_id",
        help="Détermine votre file de tri personnelle : ce que vous swipez n'affecte pas les autres utilisateurs.",
    )

    st.divider()

    st.caption("Mots-clés (pilotent la recherche, pas le score)")
    mots_cles = charger_mots_cles(client)
    cols = st.columns(3)
    for i, mot in enumerate(mots_cles):
        if cols[i % 3].button(f"✕ {mot}", key=f"retirer_mot_{mot}", use_container_width=True):
            retirer_mot_cle(client, mot)
            st.rerun()
    with st.form("ajouter_mot_cle_form", clear_on_submit=True):
        c1, c2 = st.columns([3, 1])
        nouveau_mot = c1.text_input("mot-clé", label_visibility="collapsed", placeholder="ajouter un mot-clé")
        if c2.form_submit_button("➕") and nouveau_mot.strip():
            ajouter_mot_cle(client, nouveau_mot)
            st.rerun()

    st.caption("Acheteurs suivis")
    acheteurs = charger_acheteurs_suivis(client)
    cols = st.columns(3)
    for i, nom in enumerate(acheteurs):
        if cols[i % 3].button(f"✕ {nom}", key=f"retirer_acheteur_{nom}", use_container_width=True):
            retirer_acheteur_suivi(client, nom)
            st.rerun()
    with st.form("ajouter_acheteur_form", clear_on_submit=True):
        c1, c2 = st.columns([3, 1])
        nouvel_acheteur = c1.text_input("acheteur", label_visibility="collapsed", placeholder="ajouter un acheteur")
        if c2.form_submit_button("➕") and nouvel_acheteur.strip():
            ajouter_acheteur_suivi(client, nouvel_acheteur)
            st.rerun()

    st.caption("Mots-clés lots (recherchés dans le détail des lots de chaque marché, pas seulement son objet — voir recap.md)")
    mots_cles_lots = charger_mots_cles_lots(client)
    cols = st.columns(3)
    for i, mot in enumerate(mots_cles_lots):
        if cols[i % 3].button(f"✕ {mot}", key=f"retirer_mot_lot_{mot}", use_container_width=True):
            retirer_mot_cle_lot(client, mot)
            st.rerun()
    with st.form("ajouter_mot_cle_lot_form", clear_on_submit=True):
        c1, c2 = st.columns([3, 1])
        nouveau_mot_lot = c1.text_input("mot-clé lot", label_visibility="collapsed", placeholder="ajouter un mot-clé lot")
        if c2.form_submit_button("➕") and nouveau_mot_lot.strip():
            ajouter_mot_cle_lot(client, nouveau_mot_lot)
            st.rerun()

    st.divider()

    if st.button("🔍 Lancer la recherche", type="primary", use_container_width=True):
        with st.spinner("Récupération + scoring A/B en cours..."):
            resultats = lancer_recherche_et_insertion(client)
        st.success(f"{len(resultats)} avis récupéré(s)/mis à jour.")
        st.rerun()

    if st.button("🔄 Recalculer les scores", use_container_width=True):
        with st.spinner("Recalcul en cours..."):
            nb = recalculer_tous_les_scores(client)
        st.success(f"{nb} avis recalculé(s) (A et B).")
        st.rerun()

    st.divider()

    st.caption("🧠 Modèles de préférence (A/B test)")
    afficher_poids(client, "A")
    afficher_poids(client, "B")

# --- Fiche courante ("swipe", ordre aléatoire, file propre à l'utilisateur) --
user_id = st.session_state["user_id"] or "anonyme"
nb_a_trier = compter_a_trier(client, user_id)
st.caption(f"👤 {user_id} · {nb_a_trier} appel(s) d'offre(s) restant(s) à trier pour vous (ordre aléatoire).")

offre = charger_offre_suivante(client, user_id)

if offre is None:
    st.info("🎉 Plus rien à trier pour l'instant. Cliquez sur \"Lancer la recherche\" pour en récupérer d'autres.")
else:
    with st.container(border=True):
        st.markdown(f"### {offre['objet']}")

        # Description longue de la mission (BOAMP best-effort / TED
        # description-lot — voir recupDataBaseOfficial.py). Tronquée pour
        # respecter la contrainte "tout visible sans scroller" ; peut être
        # absente (avis anciens/sans description collectée), dans ce cas on
        # n'affiche rien de plus que l'objet, comme avant.
        description = (offre.get("description") or "").strip()
        if description:
            LONGUEUR_MAX_AFFICHEE = 400
            tronquee = len(description) > LONGUEUR_MAX_AFFICHEE
            texte_affiche = description[:LONGUEUR_MAX_AFFICHEE] + "…" if tronquee else description
            st.caption(texte_affiche)

        # Lots (voir recupDataBaseOfficial.py) : sur un marché multi-lots,
        # l'objet est souvent générique — le détail utile est ici. Tronqué
        # comme la description (contrainte "tout visible sans scroller").
        lots = offre.get("lots") or []
        if lots:
            LONGUEUR_MAX_LOTS = 300
            titres_lots = [lot.get("titre") or lot.get("identifiant") or "?" for lot in lots]
            texte_lots = f"📦 {len(lots)} lot(s) : " + " · ".join(titres_lots)
            if len(texte_lots) > LONGUEUR_MAX_LOTS:
                texte_lots = texte_lots[:LONGUEUR_MAX_LOTS] + "…"
            st.caption(texte_lots)

        departements = [str(d) for d in (offre.get("departement") or [])]
        col_info, col_score_a, col_score_b = st.columns([3, 1, 1])
        with col_info:
            st.write(f"🏢 **{offre.get('acheteur') or '—'}** · 📍 {', '.join(departements) or '—'} · 🗂️ {offre.get('source') or '—'}")
            st.write(
                f"📅 Limite **{offre.get('date_limite_reponse') or '—'}** · "
                f"📄 Parution {offre.get('date_parution') or '—'} · "
                f"🔢 {offre.get('nb_versions') or 1} version(s)"
            )
            urls = [u for u in (offre.get("urls") or "").split("; ") if u]
            if urls:
                st.write(" · ".join(f"🔗 {u}" for u in urls))
        # .get(clé, 0) ne renvoie 0 que si la clé est absente — si la colonne
        # existe mais vaut NULL en base (avis jamais scoré), on affiche un
        # indicateur explicite plutôt que le "None/100" trompeur d'avant.
        score_a = offre.get("score_modele_a")
        score_b = offre.get("score_modele_b")
        with col_score_a:
            st.metric("Score A", f"{score_a}/100" if score_a is not None else "non calculé")
        with col_score_b:
            st.metric("Score B", f"{score_b}/100" if score_b is not None else "non calculé")

        commentaire = st.text_area(
            "Commentaire / raison (optionnel)",
            key=f"commentaire_{offre['id']}",
            placeholder="Pourquoi accepter ou rejeter cette offre ?",
            height=80,
        )

        col_rejeter, col_accepter = st.columns(2)
        boutons = {
            col_rejeter: "❌ Rejeter",
            col_accepter: "✅ Accepter",
        }
        for col, libelle in boutons.items():
            with col:
                if st.button(libelle, key=f"{offre['id']}_{DECISIONS[libelle]}", use_container_width=True):
                    # Le swipe (decision + raisons + swipes) est toujours
                    # enregistré même si l'entraînement du modèle échoue
                    # (voir pipeline_scoring.update_model) — on avertit sans
                    # bloquer le passage à la fiche suivante dans ce cas.
                    entrainement_ok = update_model(client, offre["id"], DECISIONS[libelle], commentaire, user_id)
                    if not entrainement_ok:
                        # st.toast survit à un st.rerun() (contrairement à
                        # st.warning, qui serait effacé immédiatement après).
                        st.toast(
                            "Décision enregistrée, mais l'entraînement du modèle a échoué "
                            "(voir le terminal) — réessayez le swipe suivant.",
                            icon="⚠️",
                        )
                    st.rerun()
