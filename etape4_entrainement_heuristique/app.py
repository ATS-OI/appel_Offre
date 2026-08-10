"""
app.py — Étape 4 : site "à la Tinder" pour entraîner l'heuristique (Streamlit)
================================================================================

Lancer avec :
    streamlit run app.py

Fonctionnalités :
  - Gestion des mots-clés et des acheteurs suivis (listes partagées en base,
    identiques pour tout le monde — voir listes_partagees.py) : ajout via un
    champ texte, retrait en cliquant sur le mot-clé/l'acheteur affiché.
  - Bouton "Lancer la recherche" : pipeline complet (récupération BOAMP/TED +
    scoring + insertion), piloté par les mots-clés/acheteurs en base.
  - Bouton "Recalculer les scores" : recalcule le score des avis déjà en
    base avec les mots-clés actuels, sans réinterroger BOAMP/TED.
  - Une fiche à la fois ("swipe") parmi les avis non encore triés
    (`decision = 'n/A'`), triés par score décroissant : objet, acheteur,
    département, date limite, score, liens, zone de commentaire, et boutons
    Accepter / Rejeter / Rejeter (pour l'instant). La décision et le
    commentaire sont enregistrés (table `raisons`), puis la fiche suivante
    apparaît automatiquement.

Connexion Supabase : fichier `.env` dans ce même dossier (voir .env.example).
"""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from supabase import Client, create_client

from InsertIntoDataBase import lancer_recherche_et_insertion, recalculer_scores
from listes_partagees import (
    ajouter_acheteur_suivi,
    ajouter_mot_cle,
    charger_acheteurs_suivis,
    charger_mots_cles,
    retirer_acheteur_suivi,
    retirer_mot_cle,
)

NOM_TABLE = "appels_offres"
NOM_TABLE_RAISONS = "raisons"

# Libellé bouton -> valeur stockée dans la colonne `decision`
DECISIONS: dict[str, str] = {
    "✅ Accepter": "accepted",
    "❌ Rejeter": "rejected",
    "⏳ Pour l'instant": "rejected (for now)",
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


def charger_offre_suivante(client: Client) -> dict | None:
    """Avis non trié (`decision = 'n/A'`) avec le score le plus élevé, ou None si la file est vide."""
    reponse = (
        client.table(NOM_TABLE)
        .select("*")
        .eq("decision", "n/A")
        .order("score", desc=True)
        .limit(1)
        .execute()
    )
    return reponse.data[0] if reponse.data else None


def compter_a_trier(client: Client) -> int:
    reponse = client.table(NOM_TABLE).select("id", count="exact").eq("decision", "n/A").execute()
    return reponse.count or 0


def enregistrer_decision(client: Client, offre_id: str, decision: str, commentaire: str) -> None:
    client.table(NOM_TABLE).update({"decision": decision}).eq("id", offre_id).execute()
    client.table(NOM_TABLE_RAISONS).insert({
        "appel_offre_id": offre_id,
        "decision": decision,
        "commentaire": commentaire or None,
    }).execute()


# =====================================================================
# Page
# =====================================================================

st.set_page_config(page_title="Entraînement heuristique — BOAMP / TED", layout="centered")
st.title("🎯 Entraînement de l'heuristique")

client = get_client()

# --- Listes partagées : mots-clés & acheteurs suivis --------------------
with st.expander("⚙️ Mots-clés et acheteurs suivis (partagés, valables pour tout le monde)", expanded=False):
    st.subheader("Mots-clés (objet)")
    mots_cles = charger_mots_cles(client)
    cols = st.columns(6)
    for i, mot in enumerate(mots_cles):
        if cols[i % 6].button(f"✕ {mot}", key=f"retirer_mot_{mot}", use_container_width=True):
            retirer_mot_cle(client, mot)
            st.rerun()
    with st.form("ajouter_mot_cle_form", clear_on_submit=True):
        c1, c2 = st.columns([4, 1])
        nouveau_mot = c1.text_input("Ajouter un mot-clé", label_visibility="collapsed", placeholder="ex. climatisation")
        if c2.form_submit_button("➕ Ajouter") and nouveau_mot.strip():
            ajouter_mot_cle(client, nouveau_mot)
            st.rerun()

    st.divider()

    st.subheader("Acheteurs suivis")
    acheteurs = charger_acheteurs_suivis(client)
    cols = st.columns(6)
    for i, nom in enumerate(acheteurs):
        if cols[i % 6].button(f"✕ {nom}", key=f"retirer_acheteur_{nom}", use_container_width=True):
            retirer_acheteur_suivi(client, nom)
            st.rerun()
    with st.form("ajouter_acheteur_form", clear_on_submit=True):
        c1, c2 = st.columns([4, 1])
        nouvel_acheteur = c1.text_input("Ajouter un acheteur suivi", label_visibility="collapsed", placeholder="ex. SIDR")
        if c2.form_submit_button("➕ Ajouter") and nouvel_acheteur.strip():
            ajouter_acheteur_suivi(client, nouvel_acheteur)
            st.rerun()

st.divider()

# --- Actions globales -----------------------------------------------------
col_recherche, col_recalcul = st.columns(2)
with col_recherche:
    if st.button("🔍 Lancer la recherche", type="primary", use_container_width=True):
        with st.spinner("Récupération BOAMP + TED en cours (peut prendre quelques dizaines de secondes)..."):
            resultats = lancer_recherche_et_insertion(client)
        st.success(f"{len(resultats)} appel(s) d'offre(s) récupéré(s) et inséré(s)/mis à jour.")
        st.rerun()
with col_recalcul:
    if st.button("🔄 Recalculer les scores", use_container_width=True):
        with st.spinner("Recalcul des scores en cours..."):
            nb = recalculer_scores(client)
        st.success(f"{nb} score(s) recalculé(s).")
        st.rerun()

st.divider()

# --- Fiche courante ("swipe") ---------------------------------------------
nb_a_trier = compter_a_trier(client)
st.caption(f"{nb_a_trier} appel(s) d'offre(s) restant(s) à trier.")

offre = charger_offre_suivante(client)

if offre is None:
    st.info("🎉 Plus rien à trier pour l'instant. Cliquez sur \"Lancer la recherche\" pour en récupérer d'autres.")
else:
    with st.container(border=True):
        st.markdown(f"### {offre['objet']}")

        departements = [str(d) for d in (offre.get("departement") or [])]
        st.write(f"🏢 **{offre.get('acheteur') or '—'}** · 📍 {', '.join(departements) or '—'}")
        st.write(
            f"📅 Limite : **{offre.get('date_limite_reponse') or '—'}** · "
            f"📄 Parution : {offre.get('date_parution') or '—'} · "
            f"🔢 {offre.get('nb_versions') or 1} version(s) fusionnée(s) · "
            f"🗂️ {offre.get('source') or '—'}"
        )
        st.metric("Score heuristique", f"{offre.get('score', 0)}/100")

        for url in (offre.get("urls") or "").split("; "):
            if url:
                st.write(f"🔗 {url}")

        commentaire = st.text_area(
            "Commentaire / raison (optionnel)",
            key=f"commentaire_{offre['id']}",
            placeholder="Pourquoi accepter ou rejeter cette offre ?",
        )

        col_rejeter, col_pourlinstant, col_accepter = st.columns(3)
        boutons = {
            col_rejeter: "❌ Rejeter",
            col_pourlinstant: "⏳ Pour l'instant",
            col_accepter: "✅ Accepter",
        }
        for col, libelle in boutons.items():
            with col:
                if st.button(libelle, key=f"{offre['id']}_{DECISIONS[libelle]}", use_container_width=True):
                    enregistrer_decision(client, offre["id"], DECISIONS[libelle], commentaire)
                    st.rerun()
