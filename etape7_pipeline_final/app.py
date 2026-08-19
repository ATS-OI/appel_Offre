"""
app.py — site (Streamlit) : le seul point d'entrée utilisateur
================================================================================

Lancer avec :
    streamlit run app.py

4 onglets :
  - "🎯 Trier" : une fiche à la fois parmi les avis que CET utilisateur n'a
    pas encore triés, tirée au hasard (pas triée par score — biaiserait le
    KNN vers les avis déjà "évidents"). Affiche pourquoi l'avis est proposé
    (mots-clés/lots/acheteur qui ont matché, voir `mots_cles_trouves`) et un
    score sur 100. Décision "👍 Intéressant" / "👎 Pas intéressant" +
    commentaire optionnel -> `raisons` (historique) + `swipes`
    (`pipeline.enregistrer_swipe`) — RAPIDE, ne recalcule rien.
  - "🆕 Nouveautés" : seulement les avis trouvés depuis la dernière
    recherche (ou depuis la 1ère recherche du jour s'il y en a eu
    plusieurs aujourd'hui — voir `pipeline.calculer_cutoff_nouveautes` :
    relancer une recherche le même jour n'efface jamais les résultats
    trouvés plus tôt dans la journée). Même outil de tri/filtre que
    l'Historique.
  - "📋 Historique" : tous les avis en base, avec VOTRE décision (chaque
    utilisateur a sa propre file, indépendante des autres) — filtrable
    (intéressant/pas intéressant) et triable (score par défaut, ou date de
    rendu). Cliquer un aperçu ouvre sa fiche complète en fenêtre modale
    (lecture seule — la décision se prend uniquement depuis "Trier").
  - "🔑 Mots-clés" : gestion des 3 listes partagées (mots-clés objet,
    mots-clés lots, acheteurs suivis) qui pilotent le périmètre de recherche
    des 6 sources et contribuent au score en cold start (voir scoring.py).

Barre latérale (visible sur tous les onglets) : identification légère,
actions globales ("🔍 Lancer la recherche" / "🔄 Recalculer les scores"),
mode de scoring actuel.
"""

from __future__ import annotations

import json
import os
import random
import re
import unicodedata
from html import escape
from pathlib import Path
from typing import Callable

import streamlit as st
from dotenv import load_dotenv
from supabase import Client, create_client

import db
import scoring
from pipeline import calculer_cutoff_nouveautes, enregistrer_swipe, lancer_recherche, recalculer_scores

NOM_TABLE = "appels_offres"
NOM_TABLE_SWIPES = "swipes"

EMOJI_DECISION_UTILISATEUR = {"like": "🟢", "dislike": "🔴", None: "⚪"}

# Valeurs internes inchangées (déjà utilisées partout en base) — seuls les
# libellés affichés changent ("accepter/rejeter" prêtait à confusion).
DECISIONS: dict[str, str] = {
    "👍 Intéressant": "accepted",
    "👎 Pas intéressant": "rejected",
}
# Variantes accentuées par lettre de base, pour surligner un mot-clé même si
# le texte de l'avis a une accentuation légèrement différente de celle
# enregistrée dans la liste (ex. mot-clé "menuiserie", avis "MENUISERIE").
_VARIANTES_ACCENTS = {
    "a": "aàâäá", "e": "eéèêë", "i": "iîïí",
    "o": "oôöó", "u": "uùûüú", "c": "cç", "n": "nñ",
}


def _sans_accent(caractere: str) -> str:
    return unicodedata.normalize("NFKD", caractere).encode("ascii", "ignore").decode("ascii") or caractere


def _motif_insensible_accents(mot: str) -> str:
    """Construit un motif regex qui matche `mot` quels que soient ses
    accents/casse (ex. "reno" -> matche "réno", "RENO", "rèno"...)."""
    morceaux = []
    for caractere in mot:
        base = _sans_accent(caractere).lower()
        if base in _VARIANTES_ACCENTS:
            variantes = _VARIANTES_ACCENTS[base]
            morceaux.append(f"[{variantes}{variantes.upper()}]")
        elif caractere.isspace():
            morceaux.append(r"\s+")
        else:
            morceaux.append(re.escape(caractere))
    return "".join(morceaux)


def surligner(texte: str, mots: list[str]) -> str:
    """Texte HTML (échappé) avec les occurrences de `mots` entourées de
    `<mark>` — utilisé pour montrer, directement dans l'objet/la description/
    les lots, POURQUOI cet avis a été retenu (voir `mots_cles_trouves`)."""
    if not texte:
        return ""
    motifs = sorted({_motif_insensible_accents(m) for m in mots if m and m.strip()}, key=len, reverse=True)
    if not motifs:
        return escape(texte)

    regex = re.compile("(" + "|".join(motifs) + ")", re.IGNORECASE)
    morceaux = []
    position = 0
    for correspondance in regex.finditer(texte):
        morceaux.append(escape(texte[position:correspondance.start()]))
        morceaux.append(
            f'<mark style="background:#fde68a; color:#111; padding:0 2px; border-radius:2px;">'
            f'{escape(correspondance.group(0))}</mark>'
        )
        position = correspondance.end()
    morceaux.append(escape(texte[position:]))
    return "".join(morceaux)


@st.cache_resource
def get_config() -> dict:
    return json.loads((Path(__file__).parent / "config.json").read_text(encoding="utf-8"))


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


# =====================================================================
# Onglet "Trier"
# =====================================================================

def _ids_deja_swipes(client: Client, user_id: str) -> set[str]:
    reponse = client.table(NOM_TABLE_SWIPES).select("appel_offre_id").eq("user_id", user_id).execute()
    return {ligne["appel_offre_id"] for ligne in reponse.data}  # type: ignore


def charger_offres_a_trier(client: Client, user_id: str) -> list[dict]:
    deja_swipes = _ids_deja_swipes(client, user_id)
    toutes_les_offres = client.table(NOM_TABLE).select("*").execute().data
    return [o for o in toutes_les_offres if o["id"] not in deja_swipes]  # type: ignore


def charger_offre_suivante(client: Client, user_id: str) -> dict | None:
    offres = charger_offres_a_trier(client, user_id)
    return random.choice(offres) if offres else None


def afficher_details_offre(offre: dict) -> None:
    """Contenu détaillé d'un avis (objet surligné, description, lots,
    mots-clés trouvés, infos, score) — lecture seule, sans les boutons de
    décision. Partagé entre la fiche de tri (voir `afficher_onglet_trier`,
    qui l'entoure des boutons de décision) et la fenêtre modale ouverte
    depuis Historique/Nouveautés (voir `_fiche_modale`)."""
    # Pourquoi cet avis est proposé : mots-clés/lots/acheteur qui ont matché
    # au moment de sa récupération (voir sources/commun.py,
    # pipeline.py::formater_pour_supabase) — utilisés ci-dessous pour
    # surligner leurs occurrences directement dans le texte de l'avis.
    mots_cles_trouves = offre.get("mots_cles_trouves") or []

    st.markdown(f"### {surligner(offre['objet'], mots_cles_trouves)}", unsafe_allow_html=True)

    description = (offre.get("description") or "").strip()
    if description:
        LONGUEUR_MAX_AFFICHEE = 400
        texte_affiche = description[:LONGUEUR_MAX_AFFICHEE] + "…" if len(description) > LONGUEUR_MAX_AFFICHEE else description
        st.markdown(
            f'<span style="font-size:0.85em; color:gray;">{surligner(texte_affiche, mots_cles_trouves)}</span>',
            unsafe_allow_html=True,
        )

    lots = offre.get("lots") or []
    if lots:
        LONGUEUR_MAX_LOTS = 300
        titres_lots = [lot.get("titre") or lot.get("identifiant") or "?" for lot in lots]
        texte_lots = f"📦 {len(lots)} lot(s) : " + " · ".join(titres_lots)
        if len(texte_lots) > LONGUEUR_MAX_LOTS:
            texte_lots = texte_lots[:LONGUEUR_MAX_LOTS] + "…"
        st.markdown(
            f'<span style="font-size:0.85em; color:gray;">{surligner(texte_lots, mots_cles_trouves)}</span>',
            unsafe_allow_html=True,
        )

    # Encadré explicite en plus du surlignage : certains mots-clés
    # (ex. acheteur suivi) ne sont pas forcément visibles dans le texte
    # affiché ci-dessus (objet/description/lots tronqués) — ce message
    # reste la source de vérité de "pourquoi cet avis est proposé".
    if mots_cles_trouves:
        st.info("🔦 Cet avis a été choisi car il contient les mots-clés : **" + ", ".join(mots_cles_trouves) + "**")

    departements = [str(d) for d in (offre.get("departement") or [])]
    col_info, col_score = st.columns([4, 1])
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
    score = offre.get("score")
    with col_score:
        st.metric("Score", f"{score}/100" if score is not None else "non calculé")


def afficher_onglet_trier(client: Client, user_id: str) -> None:
    nb_a_trier = len(charger_offres_a_trier(client, user_id))
    st.caption(f"👤 {user_id} · {nb_a_trier} appel(s) d'offre(s) restant(s) à trier pour vous (ordre aléatoire).")

    # L'offre affichée doit rester STABLE d'un rerun à l'autre (Streamlit
    # relance tout le script à chaque interaction). Si on retirait un
    # nouvel avis au hasard à chaque rerun, le rerun déclenché PAR le clic
    # sur un bouton de décision tirerait une AUTRE offre avant que le clic
    # soit traité : le bouton affiché porterait alors la clé de cette
    # nouvelle offre, pas celle sur laquelle l'utilisateur a cliqué — le
    # clic serait perdu silencieusement.
    if (
        st.session_state.get("offre_courante") is None
        or st.session_state.get("offre_courante_user_id") != user_id
    ):
        st.session_state["offre_courante"] = charger_offre_suivante(client, user_id)
        st.session_state["offre_courante_user_id"] = user_id

    offre = st.session_state["offre_courante"]

    if offre is None:
        st.info("🎉 Plus rien à trier pour l'instant. Cliquez sur \"Lancer la recherche\" pour en récupérer d'autres.")
        return

    with st.container(border=True):
        afficher_details_offre(offre)

        commentaire = st.text_area(
            "Commentaire / raison (optionnel)",
            key=f"commentaire_{offre['id']}",
            placeholder="Pourquoi cette offre est-elle intéressante ou non ?",
            height=80,
        )

        col_pas_interessant, col_interessant = st.columns(2)
        boutons = {col_pas_interessant: "👎 Pas intéressant", col_interessant: "👍 Intéressant"}
        for col, libelle in boutons.items():
            with col:
                if st.button(libelle, key=f"{offre['id']}_{DECISIONS[libelle]}", use_container_width=True):
                    ok = enregistrer_swipe(client, offre["id"], DECISIONS[libelle], commentaire, user_id)
                    if ok:
                        st.toast(f"{libelle} — enregistré.", icon="✅")
                    else:
                        st.toast("Échec de l'enregistrement (voir le terminal) — réessayez.", icon="⚠️")
                    st.session_state["offre_courante"] = None
                    st.rerun()


# =====================================================================
# Historique / Nouveautés — liste triable/filtrable, avec fiche détaillée
# =====================================================================

def charger_toutes_les_offres_avec_decision(client: Client, user_id: str) -> list[dict]:
    """Tous les avis en base, avec la décision de CET utilisateur (`like`/
    `dislike`/`None`) — pas la décision globale `appels_offres.decision`
    (informative, tous utilisateurs confondus) : chaque utilisateur a sa
    propre file, l'historique doit refléter SES décisions à lui. Pas de tri
    fixe ici (voir `afficher_liste_offres`, où le tri est un choix affiché
    à l'utilisateur)."""
    offres = client.table(NOM_TABLE).select("*").execute().data
    swipes = client.table(NOM_TABLE_SWIPES).select("appel_offre_id,decision").eq("user_id", user_id).execute().data
    decisions = {s["appel_offre_id"]: s["decision"] for s in swipes}
    for o in offres:
        o["decision_utilisateur"] = decisions.get(o["id"])
    return offres


def charger_offre_par_id(client: Client, offre_id: str) -> dict | None:
    reponse = client.table(NOM_TABLE).select("*").eq("id", offre_id).limit(1).execute()
    return reponse.data[0] if reponse.data else None


def _fermer_fiche_modale() -> None:
    st.session_state["offre_modale_id"] = None


@st.dialog("Détails de l'avis", width="large", on_dismiss=_fermer_fiche_modale)
def _fiche_modale(offre: dict) -> None:
    afficher_details_offre(offre)
    st.divider()
    # Bouton explicite en plus de `on_dismiss` (qui couvre le X natif/Échap/
    # clic à l'extérieur) : les deux chemins vident `offre_modale_id`, sinon
    # la fiche se rouvrirait aussitôt au rerun suivant.
    if st.button("✕ Fermer"):
        _fermer_fiche_modale()
        st.rerun()


def afficher_liste_offres(offres: list[dict], cle_prefix: str) -> None:
    """Filtre, trie et affiche une liste d'avis — partagé entre Historique
    et Nouveautés. Chaque aperçu est un bouton cliquable (indicateur couleur
    en emoji) qui ouvre la fiche complète (voir `afficher_details_offre`)
    dans une fenêtre modale, en lecture seule."""
    col_filtre, col_tri = st.columns(2)
    with col_filtre:
        filtre = st.segmented_control(
            "Filtrer", ["Toutes", "👍 Intéressant", "👎 Pas intéressant"],
            default="Toutes", required=True, key=f"{cle_prefix}_filtre",
        )
    with col_tri:
        tri = st.segmented_control(
            "Trier par", ["🎯 Score", "📅 Date de rendu"],
            default="🎯 Score", required=True, key=f"{cle_prefix}_tri",
        )

    if filtre == "👍 Intéressant":
        offres = [o for o in offres if o["decision_utilisateur"] == "like"]
    elif filtre == "👎 Pas intéressant":
        offres = [o for o in offres if o["decision_utilisateur"] == "dislike"]

    if tri == "🎯 Score":
        offres = sorted(offres, key=lambda o: o.get("score") if o.get("score") is not None else -1, reverse=True)
    else:
        offres = sorted(offres, key=lambda o: o.get("date_limite_reponse") or "9999-99-99")

    st.caption(f"{len(offres)} avis.")
    if not offres:
        st.info("Aucun avis à afficher pour ce filtre.")
        return

    for o in offres:
        emoji = EMOJI_DECISION_UTILISATEUR[o["decision_utilisateur"]]
        score = o.get("score")
        score_txt = f"{score}/100" if score is not None else "non calculé"
        nb_lots = len(o.get("lots") or [])
        departements = ", ".join(str(d) for d in (o.get("departement") or [])) or "—"

        with st.container(border=True):
            if st.button(
                f"{emoji} {o['objet']}",
                key=f"{cle_prefix}_apercu_{o['id']}", use_container_width=True,
            ):
                st.session_state["offre_modale_id"] = o["id"]
                st.rerun()
            # st.markdown plutôt que st.caption : st.caption grise
            # systématiquement le texte (voulu ailleurs pour du texte
            # secondaire, mais peu lisible ici) — un texte normal reste
            # net dans les deux thèmes (noir en clair, blanc en sombre).
            st.markdown(
                f"📅 {o.get('date_limite_reponse') or '—'}  ·  🎯 {score_txt}  ·  "
                f"📦 {nb_lots} lot(s)  ·  📍 {departements}  ·  "
                f"🗂️ {o.get('source') or '—'}  ·  🏢 {o.get('acheteur') or '—'}"
            )
            description = (o.get("description") or "").strip()
            if description:
                LONGUEUR_MAX_APERCU = 200
                if len(description) > LONGUEUR_MAX_APERCU:
                    description = description[:LONGUEUR_MAX_APERCU] + "…"
                st.markdown(description)


def afficher_onglet_historique(client: Client, user_id: str) -> None:
    afficher_liste_offres(charger_toutes_les_offres_avec_decision(client, user_id), cle_prefix="historique")


def afficher_onglet_nouveautes(client: Client, user_id: str) -> None:
    cutoff = calculer_cutoff_nouveautes(client)
    if cutoff is None:
        st.info("🎉 Aucune recherche n'a encore été lancée. Cliquez sur \"Lancer la recherche\" pour commencer.")
        return

    offres = charger_toutes_les_offres_avec_decision(client, user_id)
    offres = [o for o in offres if (o.get("created_at") or "") >= cutoff]
    st.caption(f"Avis trouvés depuis la dernière recherche (ou depuis la 1ère recherche du jour s'il y en a eu plusieurs aujourd'hui) : {cutoff[:16].replace('T', ' ')} UTC.")
    afficher_liste_offres(offres, cle_prefix="nouveautes")


# =====================================================================
# Onglet "Mots-clés"
# =====================================================================

def afficher_colonne_liste(client: Client, titre: str, valeurs: list[str], cle: str, ajouter_fn, retirer_fn) -> None:
    st.markdown(f"**{titre}**")
    cle_suppr = f"mode_suppr_{cle}"
    cle_ajout = f"mode_ajout_{cle}"
    st.session_state.setdefault(cle_suppr, False)
    st.session_state.setdefault(cle_ajout, False)

    if not valeurs:
        st.caption("— aucune valeur —")
    elif st.session_state[cle_suppr]:
        for valeur in valeurs:
            c1, c2 = st.columns([5, 1])
            c1.write(valeur)
            if c2.button("✕", key=f"suppr_{cle}_{valeur}"):
                retirer_fn(client, valeur)
                st.rerun()
    else:
        st.write(", ".join(valeurs))

    c1, c2 = st.columns(2)
    if c1.button("✅ Terminé" if st.session_state[cle_suppr] else "🗑️ Supprimer", key=f"toggle_suppr_{cle}", use_container_width=True):
        st.session_state[cle_suppr] = not st.session_state[cle_suppr]
        st.rerun()
    if c2.button("➕ Ajouter", key=f"toggle_ajout_{cle}", use_container_width=True):
        st.session_state[cle_ajout] = not st.session_state[cle_ajout]
        st.rerun()

    if st.session_state[cle_ajout]:
        with st.form(f"form_ajout_{cle}", clear_on_submit=True):
            nouvelle_valeur = st.text_input("valeur", label_visibility="collapsed", placeholder="nouvelle valeur")
            if st.form_submit_button("Confirmer") and nouvelle_valeur.strip():
                ajouter_fn(client, nouvelle_valeur)
                st.rerun()


def creer_callback_progression(statut) -> Callable[[str], None]:
    """Callback `on_progress` pour un bloc `st.status` qui ne s'allonge PAS
    à l'infini : les messages "compteur" (ex. "Score 40/92...", répétés à
    chaque tick) mettent à jour une SEULE ligne sur place ; seules les
    étapes importantes ("Connexion...", "Extraction...", ...) ajoutent une
    nouvelle ligne — sinon la fenêtre grossirait sans fin sur une grosse
    recherche (des dizaines de lignes de compteur en plus des étapes)."""
    compteur = st.empty()

    def on_progress(message: str) -> None:
        if re.search(r"\d+\s*/\s*\d+", message):
            compteur.write(message)
        else:
            statut.write(message)

    return on_progress


def afficher_onglet_mots_cles(client: Client) -> None:
    col1, col2, col3 = st.columns(3)
    with col1:
        afficher_colonne_liste(
            client, "Mots-clés (objet)", db.charger_mots_cles(client),
            "mots_cles", db.ajouter_mot_cle, db.retirer_mot_cle,
        )
    with col2:
        afficher_colonne_liste(
            client, "Mots-clés (lots)", db.charger_mots_cles_lots(client),
            "mots_cles_lots", db.ajouter_mot_cle_lot, db.retirer_mot_cle_lot,
        )
    with col3:
        afficher_colonne_liste(
            client, "Acheteurs suivis", db.charger_acheteurs_suivis(client),
            "acheteurs", db.ajouter_acheteur_suivi, db.retirer_acheteur_suivi,
        )


# =====================================================================
# Page
# =====================================================================

st.set_page_config(page_title="Appels d'offres — La Réunion / Mayotte", layout="wide")
config = get_config()
client = get_client()

if "user_id" not in st.session_state:
    st.session_state["user_id"] = "anonyme"
if "erreurs_sources" not in st.session_state:
    st.session_state["erreurs_sources"] = []

# --- Barre latérale : identification + actions globales -----------------
with st.sidebar:
    st.header("⚙️ Réglages")

    st.text_input(
        "👤 Votre nom",
        key="user_id",
        help="Détermine votre file de tri personnelle : ce que vous triez n'affecte pas les autres utilisateurs.",
    )

    st.divider()

    # `st.status` : les deux actions ci-dessous peuvent prendre plusieurs
    # dizaines de secondes à quelques minutes (embeddings, appels réseau aux
    # 6 sources...) — affiche chaque étape en direct pour que l'utilisateur
    # voie que ça travaille plutôt que d'abandonner en pensant que la page
    # est figée.
    if st.button("🔍 Lancer la recherche", type="primary", use_container_width=True):
        st.session_state["erreurs_sources"] = []
        with st.status("🔍 Recherche en cours...", expanded=True) as statut:
            resultats = lancer_recherche(
                client, config["departements"], config["seulement_ouverts"],
                on_progress=creer_callback_progression(statut),
                on_erreur=lambda nom, message: st.session_state["erreurs_sources"].append(message),
            )
            statut.update(label=f"✅ Terminé — {len(resultats)} avis récupéré(s)/mis à jour.", state="complete")
        st.session_state["offre_courante"] = None  # nouvelles données -> nouveau tirage
        st.rerun()

    if st.button("🔄 Recalculer les scores", use_container_width=True):
        with st.status("🔄 Recalcul des scores en cours...", expanded=True) as statut:
            nb = recalculer_scores(client, on_progress=creer_callback_progression(statut))
            statut.update(label=f"✅ Terminé — {nb} avis recalculé(s).", state="complete")
        st.session_state["offre_courante"] = None
        st.rerun()

    st.divider()
    nb_swipes_total = len(client.table(NOM_TABLE_SWIPES).select("id").execute().data)
    if nb_swipes_total < scoring.SEUIL_COLD_START:
        st.caption(f"🧭 Mode actuel : heuristique (cold start, {nb_swipes_total}/{scoring.SEUIL_COLD_START} avis triés).")
    else:
        st.caption(f"🧭 Mode actuel : KNN (similarité aux {nb_swipes_total} avis déjà triés).")

# --- Erreurs de récupération (persistent le temps de la session) --------
for message in st.session_state.get("erreurs_sources", []):
    st.error(message)

# --- Fiche détaillée (Historique/Nouveautés -> clic sur un aperçu) ------
# Vérifié avant les onglets : la fenêtre modale doit s'ouvrir quel que soit
# l'onglet affiché au moment du clic (le clic déclenche déjà un st.rerun()).
if st.session_state.get("offre_modale_id"):
    offre_modale = charger_offre_par_id(client, st.session_state["offre_modale_id"])
    if offre_modale is None:
        st.session_state["offre_modale_id"] = None  # avis supprimé entre-temps (ex. expiré)
    else:
        _fiche_modale(offre_modale)

# --- Onglets --------------------------------------------------------------
user_id = st.session_state["user_id"] or "anonyme"
tab_trier, tab_nouveautes, tab_historique, tab_mots_cles = st.tabs(
    ["🎯 Trier", "🆕 Nouveautés", "📋 Historique", "🔑 Mots-clés"]
)

with tab_trier:
    afficher_onglet_trier(client, user_id)

with tab_nouveautes:
    afficher_onglet_nouveautes(client, user_id)

with tab_historique:
    afficher_onglet_historique(client, user_id)

with tab_mots_cles:
    afficher_onglet_mots_cles(client)
