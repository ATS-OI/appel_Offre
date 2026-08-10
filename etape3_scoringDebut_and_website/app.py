"""
app.py — Étape 3 : site de suivi des appels d'offres (Streamlit)
================================================================================

Lancer avec :
    streamlit run app.py

Fonctionnalités :
  - Bouton "Lancer la recherche" : exécute le pipeline complet (récupération
    BOAMP/TED + scoring + insertion dans Supabase — voir InsertIntoDataBase.py).
  - Liste des appels d'offres en base, triée par date limite de réponse.
  - Pour chaque offre, boutons pour assigner la décision : Accepter / Rejeter /
    Rejeter (pour l'instant) / Réinitialiser (n/A).

Connexion Supabase : fichier `.env` dans ce même dossier (voir .env.example).
"""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from supabase import Client, create_client

from InsertIntoDataBase import lancer_recherche_et_insertion

NOM_TABLE = "appels_offres"

# Libellé bouton -> valeur stockée dans la colonne `decision`
DECISIONS: dict[str, str] = {
    "✅ Accepter": "accepted",
    "❌ Rejeter": "rejected",
    "⏳ Rejeter (pour l'instant)": "rejected (for now)",
    "↩️ Réinitialiser": "n/A",
}

COULEUR_DECISION = {
    "accepted": "green",
    "rejected": "red",
    "rejected (for now)": "orange",
    "n/A": "gray",
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


def charger_offres(client: Client) -> list[dict]:
    reponse = client.table(NOM_TABLE).select("*").order("date_limite_reponse", desc=False).execute()
    return reponse.data


def mettre_a_jour_decision(client: Client, id_offre: str, decision: str) -> None:
    client.table(NOM_TABLE).update({"decision": decision}).eq("id", id_offre).execute()


# =====================================================================
# Page
# =====================================================================

st.set_page_config(page_title="Appels d'offres — BOAMP / TED", layout="wide")
st.title("📋 Appels d'offres — La Réunion / Mayotte")

client = get_client()

if st.button("🔍 Lancer la recherche", type="primary"):
    with st.spinner("Récupération BOAMP + TED en cours (peut prendre quelques dizaines de secondes)..."):
        resultats = lancer_recherche_et_insertion(client)
    st.success(f"{len(resultats)} appel(s) d'offre(s) récupéré(s) et inséré(s)/mis à jour.")
    st.rerun()

st.divider()

offres = charger_offres(client)

col_filtre, col_info = st.columns([2, 3])
with col_filtre:
    filtre_decision = st.selectbox(
        "Filtrer par décision",
        ["Toutes"] + list(dict.fromkeys(DECISIONS.values())),
    )
with col_info:
    st.caption(f"{len(offres)} appel(s) d'offre(s) en base.")

if filtre_decision != "Toutes":
    offres = [o for o in offres if (o.get("decision") or "n/A") == filtre_decision]

if not offres:
    st.info("Aucun appel d'offre à afficher. Cliquez sur \"Lancer la recherche\" pour en récupérer.")

for offre in offres:
    with st.container(border=True):
        col_details, col_actions = st.columns([3, 1])

        with col_details:
            st.markdown(f"**{offre['objet']}**")
            # `departement` peut revenir de Supabase comme liste d'entiers ou de
            # strings selon le type de colonne côté base -> on force en str.
            departements = [str(d) for d in (offre.get("departement") or [])]
            st.write(f"🏢 {offre.get('acheteur') or '—'} · 📍 {', '.join(departements) or '—'}")
            st.write(
                f"📅 Limite : **{offre.get('date_limite_reponse') or '—'}** · "
                f"📄 Parution : {offre.get('date_parution') or '—'} · "
                f"🔢 {offre.get('nb_versions') or 1} version(s) fusionnée(s) · "
                f"⭐ Score : **{offre.get('score', 0)}/100** · "
                f"🗂️ {offre.get('source') or '—'}"
            )
            for url in (offre.get("urls") or "").split("; "):
                if url:
                    st.write(f"🔗 {url}")
            decision_actuelle = offre.get("decision") or "n/A"
            st.markdown(f":{COULEUR_DECISION.get(decision_actuelle, 'gray')}[Décision : **{decision_actuelle}**]")

        with col_actions:
            for libelle, valeur in DECISIONS.items():
                desactive = decision_actuelle == valeur
                if st.button(libelle, key=f"{offre['id']}_{valeur}", disabled=desactive, use_container_width=True):
                    mettre_a_jour_decision(client, offre["id"], valeur)
                    st.rerun()
