#!/usr/bin/env python3
"""Page de suivi de la chaîne de développement, sur http://127.0.0.1:8770/.

Lit `tickets/ETAT.md` et `.chaine/journal.jsonl` (écrit par
`scripts/boucle_executant.py`) et affiche l'état des tickets, la session en
cours, les mesures de la vague et l'activité récente. Lecture seule, aucune
dépendance hors bibliothèque standard, aucune ressource externe.

Usage :
    python3 scripts/suivi.py            # premier plan
    nohup python3 scripts/suivi.py >/dev/null 2>&1 &   # détaché
"""

from __future__ import annotations

import contextlib
import datetime as dt
import html
import json
import os
import re
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
DOSSIER = RACINE / ".chaine"
JOURNAL = DOSSIER / "journal.jsonl"
FICHIER_PID = DOSSIER / "boucle.pid"
ETAT = RACINE / "tickets" / "ETAT.md"
ADRESSE = "127.0.0.1"
PORT = 8770
RE_ID = re.compile(r"V\d+-(?:\d+|C\d+)")


def lire_tickets() -> list[dict]:
    tickets = []
    if not ETAT.exists():
        return tickets
    for ligne in ETAT.read_text(encoding="utf-8").splitlines():
        cellules = [c.strip() for c in ligne.strip().strip("|").split("|")]
        if len(cellules) == 6 and RE_ID.fullmatch(cellules[0]):
            tickets.append(
                dict(zip(("id", "titre", "depend", "etat", "tentatives", "commit"), cellules, strict=True))
            )
    return tickets


def lire_journal() -> list[dict]:
    if not JOURNAL.exists():
        return []
    evenements = []
    for ligne in JOURNAL.read_text(encoding="utf-8").splitlines():
        try:
            evenements.append(json.loads(ligne))
        except ValueError:
            continue
    return evenements


def port_ouvert(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((ADRESSE, port)) == 0


def boucle_vivante() -> bool:
    try:
        os.kill(int(FICHIER_PID.read_text()), 0)
        return True
    except (OSError, ValueError):
        return False


def duree(secondes: float | None) -> str:
    if secondes is None:
        return ""
    secondes = int(secondes)
    h, reste = divmod(secondes, 3600)
    m, s = divmod(reste, 60)
    return f"{h} h {m:02d} min" if h else f"{m} min {s:02d} s"


def depuis(horodatage: str) -> float:
    return (dt.datetime.now().astimezone() - dt.datetime.fromisoformat(horodatage)).total_seconds()


def construire_etat() -> dict:
    """Assemble tout ce que la page affiche. Sert aussi `/etat.json`."""
    evenements = lire_journal()
    tickets = lire_tickets()
    # Dernière exécution de la boucle : à partir du dernier « boucle_debut ».
    debuts = [i for i, e in enumerate(evenements) if e["evt"] == "boucle_debut"]
    vague = evenements[debuts[-1]].get("vague") if debuts else None
    en_cours = None
    sessions: list[dict] = []
    ouverte: dict | None = None
    for e in evenements:
        if e["evt"] == "session_debut":
            ouverte = {"ticket": e["ticket"], "tentative": e["tentative"], "debut": e["t"]}
        elif e["evt"] == "session_fin" and ouverte:
            sessions.append({**ouverte, **e})
            ouverte = None
        elif e["evt"] in ("boucle_arret", "boucle_fin", "boucle_erreur"):
            ouverte = None
    vivante = boucle_vivante()
    if ouverte and vivante:
        en_cours = {**ouverte, "depuis_s": depuis(ouverte["debut"])}

    def somme(cle: str) -> int:
        return sum(int(s.get(cle) or 0) for s in sessions)

    de_la_vague = [t for t in tickets if vague is not None and t["id"].startswith(f"V{vague}-")]
    faits = [t for t in de_la_vague if t["etat"] == "fait"]
    mesures = {
        "vague": vague,
        "tickets": len(de_la_vague),
        "faits": len(faits),
        "faits_premiere_tentative": len([t for t in faits if t["tentatives"] == "1"]),
        "bloques": len([t for t in de_la_vague if t["etat"] == "bloqué"]),
        "sessions": len(sessions),
        "duree_sessions_s": somme("duree_s"),
        "appels_outils": somme("appels_outils"),
        "erreurs_outils": somme("erreurs_outils"),
        "refus_permission": somme("refus_permission"),
        "tokens_entree": somme("tokens_entree"),
        "tokens_sortie": somme("tokens_sortie"),
        "interventions": [e for e in evenements if e["evt"] == "intervention"],
        "gardes": [e for e in evenements if e["evt"] == "garde"],
    }
    if debuts:
        fin = next(
            (e for e in reversed(evenements) if e["evt"] in ("boucle_fin", "boucle_arret")),
            None,
        )
        premier = evenements[debuts[0]]["t"]
        if vivante or fin is None:
            mesures["duree_totale_s"] = depuis(premier)
        else:
            mesures["duree_totale_s"] = (
                dt.datetime.fromisoformat(fin["t"]) - dt.datetime.fromisoformat(premier)
            ).total_seconds()
    return {
        "genere": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "branche": (RACINE / ".git" / "HEAD").read_text().strip().rsplit("/", 1)[-1],
        "chaine": {
            "modele_8080": port_ouvert(8080),
            "passerelle_4000": port_ouvert(4000),
            "boucle": vivante,
        },
        "tickets": tickets,
        "en_cours": en_cours,
        "mesures": mesures,
        "sessions": sessions,
        "activite": evenements[-40:],
    }


STYLE = """
:root { --fond: #FFFAF0; --texte: #0F0E2B; --accent: #1F24E9; --doux: #6b6a80;
        --trait: #e4dccb; --ok: #1a7f4b; --ko: #b3261e; --attente: #8a6d00; }
* { box-sizing: border-box; }
body { margin: 0; padding: 24px 16px 48px; background: var(--fond); color: var(--texte);
       font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
main { max-width: 1040px; margin: 0 auto; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 16px; margin: 32px 0 8px; }
p.note { color: var(--doux); margin: 0 0 16px; }
table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
th, td { text-align: left; padding: 6px 10px 6px 0; border-bottom: 1px solid var(--trait);
         vertical-align: top; }
th { font-weight: 600; color: var(--doux); font-size: 13px; }
.defile { overflow-x: auto; }
.pastille { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 13px;
            border: 1px solid currentColor; white-space: nowrap; }
.ok { color: var(--ok); } .ko { color: var(--ko); } .attente { color: var(--attente); }
.neutre { color: var(--doux); } .actif { color: var(--accent); }
.cartes { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
.carte { border: 1px solid var(--trait); border-radius: 8px; padding: 10px 12px; background: #fff; }
.carte b { display: block; font-size: 22px; }
.carte span { color: var(--doux); font-size: 13px; }
code, .mono { font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace; }
td.mono { word-break: break-word; }
.encours { border: 1px solid var(--accent); border-radius: 8px; padding: 12px 14px; background: #fff; }
"""

LIBELLES = {
    "boucle_debut": "Début de la boucle",
    "boucle_fin": "Fin de la boucle",
    "boucle_arret": "Boucle interrompue",
    "boucle_erreur": "Erreur de la boucle",
    "session_debut": "Début de session",
    "session_init": "Session initialisée",
    "session_fin": "Fin de session",
    "outil": "Outil",
    "outil_erreur": "Erreur d'outil",
    "texte": "Texte",
    "garde": "Fichier protégé restauré",
    "hors_liste": "Fichier hors liste du ticket",
    "install": "make install",
    "verif": "make check",
    "commit": "Commit",
    "bloque": "Ticket bloqué",
    "saute": "Ticket sauté",
    "intervention": "Intervention",
    "remise": "Ticket remis à faire",
    "nettoyage": "Arbre mis de côté",
    "alerte": "Alerte",
}


def e(valeur: object) -> str:
    return html.escape(str(valeur if valeur is not None else ""))


def pastille(texte: str, classe: str) -> str:
    return f'<span class="pastille {classe}">{e(texte)}</span>'


def classe_etat(etat: str) -> str:
    return {"fait": "ok", "bloqué": "ko"}.get(etat, "neutre")


def detail_evenement(ev: dict) -> str:
    genre = ev["evt"]
    if genre in ("outil", "outil_erreur", "texte"):
        prefixe = f"{ev.get('nom')} · " if ev.get("nom") else ""
        return prefixe + str(ev.get("resume", ""))
    if genre == "verif":
        verdict = "réussi" if ev.get("ok") else f"échec ({ev.get('motif') or ev.get('code')})"
        return f"{verdict} · contrats {ev.get('contrats')}"
    if genre == "session_fin":
        return (
            f"{duree(ev.get('duree_s'))}, {ev.get('appels_outils')} appels d'outils, "
            f"{ev.get('erreurs_outils')} erreurs, code {ev.get('code')}"
            + (" · délai dépassé" if ev.get("delai_depasse") else "")
        )
    if genre == "intervention":
        return f"{ev.get('auteur')} : {ev.get('texte')}"
    reste = {k: v for k, v in ev.items() if k not in ("t", "evt", "ticket", "tentative")}
    return json.dumps(reste, ensure_ascii=False) if reste else ""


def page() -> str:
    etat = construire_etat()
    m = etat["mesures"]
    chaine = etat["chaine"]
    vague = m["vague"]
    morceaux = [
        "<!doctype html><html lang='fr'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        "<meta http-equiv='refresh' content='5'>",
        f"<title>Suivi de la chaîne</title><style>{STYLE}</style></head><body><main>",
        "<h1>Suivi de la chaîne Cabinet</h1>",
        (
            f"<p class='note'>Branche <code>{e(etat['branche'])}</code>. "
            f"Page générée à {e(etat['genere'][11:19])}, rechargée toutes les 5 secondes.</p>"
        ),
        "<p>",
        pastille("modèle 8080 " + ("répond" if chaine["modele_8080"] else "muet"),
                 "ok" if chaine["modele_8080"] else "ko"),
        " ",
        pastille("passerelle 4000 " + ("répond" if chaine["passerelle_4000"] else "muette"),
                 "ok" if chaine["passerelle_4000"] else "ko"),
        " ",
        pastille("boucle " + ("en marche" if chaine["boucle"] else "à l'arrêt"),
                 "actif" if chaine["boucle"] else "neutre"),
        "</p>",
    ]

    if etat["en_cours"]:
        c = etat["en_cours"]
        morceaux.append(
            f"<div class='encours'><b>Session en cours : {e(c['ticket'])}</b>, "
            f"tentative {e(c['tentative'])} sur 3, depuis {e(duree(c['depuis_s']))}.</div>"
        )

    if vague is not None:
        morceaux.append(f"<h2>Mesures de la vague {e(vague)}</h2><div class='cartes'>")
        cartes = [
            (f"{m['faits']} / {m['tickets']}", "tickets faits"),
            (m["faits_premiere_tentative"], "faits à la première tentative"),
            (m["bloques"], "tickets bloqués"),
            (len(m["interventions"]), "interventions notées"),
            (m["sessions"], "sessions terminées"),
            (duree(m.get("duree_totale_s")), "durée depuis le premier lancement"),
            (duree(m["duree_sessions_s"]), "temps passé en session"),
            (f"{m['appels_outils']} / {m['erreurs_outils']}", "appels d'outils / erreurs"),
            (m["refus_permission"], "refus de permission"),
            (len(m["gardes"]), "sessions avec fichier protégé touché"),
            (f"{m['tokens_entree']:,}".replace(",", " "), "tokens en entrée"),
            (f"{m['tokens_sortie']:,}".replace(",", " "), "tokens en sortie"),
        ]
        for valeur, libelle in cartes:
            morceaux.append(f"<div class='carte'><b>{e(valeur)}</b><span>{e(libelle)}</span></div>")
        morceaux.append("</div>")
    else:
        morceaux.append("<p class='note'>La boucle n'a pas encore été lancée.</p>")

    montres = [t for t in etat["tickets"] if vague is None or t["id"].startswith(f"V{vague}-")]
    morceaux.append("<h2>Tickets</h2><div class='defile'><table><tr><th>Ticket</th><th>Titre</th>"
                    "<th>Dépend de</th><th>État</th><th>Tentatives</th><th>Commit</th></tr>")
    for t in montres:
        morceaux.append(
            f"<tr><td class='mono'>{e(t['id'])}</td><td>{e(t['titre'])}</td>"
            f"<td class='mono'>{e(t['depend'])}</td>"
            f"<td>{pastille(t['etat'], classe_etat(t['etat']))}</td>"
            f"<td>{e(t['tentatives'])}</td><td class='mono'>{e(t['commit'])}</td></tr>"
        )
    morceaux.append("</table></div>")

    if etat["sessions"]:
        morceaux.append("<h2>Sessions</h2><div class='defile'><table><tr><th>Ticket</th>"
                        "<th>Tentative</th><th>Durée</th><th>Tours</th><th>Outils</th>"
                        "<th>Erreurs</th><th>Refus</th><th>Tokens entrée / sortie</th></tr>")
        for s in etat["sessions"]:
            morceaux.append(
                f"<tr><td class='mono'>{e(s['ticket'])}</td><td>{e(s['tentative'])}</td>"
                f"<td>{e(duree(s.get('duree_s')))}</td><td>{e(s.get('tours'))}</td>"
                f"<td>{e(s.get('appels_outils'))}</td><td>{e(s.get('erreurs_outils'))}</td>"
                f"<td>{e(s.get('refus_permission'))}</td>"
                f"<td>{e(s.get('tokens_entree'))} / {e(s.get('tokens_sortie'))}</td></tr>"
            )
        morceaux.append("</table></div>")

    morceaux.append("<h2>Activité récente</h2><div class='defile'><table>"
                    "<tr><th>Heure</th><th>Ticket</th><th>Évènement</th><th>Détail</th></tr>")
    for ev in reversed(etat["activite"]):
        ticket = ev.get("ticket", "")
        if ev.get("tentative"):
            ticket = f"{ticket} t{ev['tentative']}"
        morceaux.append(
            f"<tr><td class='mono'>{e(ev['t'][11:19])}</td><td class='mono'>{e(ticket)}</td>"
            f"<td>{e(LIBELLES.get(ev['evt'], ev['evt']))}</td>"
            f"<td class='mono'>{e(detail_evenement(ev)[:400])}</td></tr>"
        )
    morceaux.append("</table></div></main></body></html>")
    return "".join(morceaux)


class Gestionnaire(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        try:
            if self.path.split("?")[0] == "/etat.json":
                corps = json.dumps(construire_etat(), ensure_ascii=False).encode()
                genre = "application/json; charset=utf-8"
            elif self.path.split("?")[0] == "/":
                corps, genre = page().encode(), "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # la page ne doit pas tomber sur un journal abîmé
            corps = f"Erreur de lecture : {exc}".encode()
            genre = "text/plain; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", genre)
        self.send_header("Content-Length", str(len(corps)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corps)

    def log_message(self, *_args: object) -> None:
        return


def main() -> int:
    try:
        serveur = ThreadingHTTPServer((ADRESSE, PORT), Gestionnaire)
    except OSError as exc:
        print(f"Port {PORT} indisponible : {exc}", file=sys.stderr)
        return 1
    print(f"Suivi sur http://{ADRESSE}:{PORT}/", flush=True)
    with contextlib.suppress(KeyboardInterrupt):
        serveur.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
