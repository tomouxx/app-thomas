#!/usr/bin/env python3
"""Relecteurs automatiques de Cabinet : six agents Claude relisent chaque ticket commité.

Après chaque commit de la boucle (`scripts/boucle_executant.py`), cinq relecteurs
relisent le ticket en parallèle, chacun sous un angle, puis un vérificateur reprend
chacun de leurs constats dans le code et le confirme ou le rejette.

| Agent        | Angle                                                                  |
| ------------ | ---------------------------------------------------------------------- |
| conformite   | le code fait-il ce que le ticket et `specs/` demandent, critère par critère |
| principes    | les sept principes de `specs/00 §5` et les contrôles de `specs/09 §5`  |
| tests        | les tests unitaires de Qwen testent-ils vraiment quelque chose         |
| code         | conventions de `specs/01 §5`, arborescence, lisibilité                 |
| conduite     | ce que Qwen a fait pendant sa session : refus, contournements, oublis  |
| verificateur | confirme ou rejette chaque constat des cinq autres                     |

Les relecteurs observent et ne corrigent rien. En vague 0, leurs constats ne sont
pas renvoyés à l'exécutant : ils nourrissent la phase B de `REVIEW.md`, qui décide
des tickets de correction. Ils travaillent sur une copie figée du commit
(`git worktree`), en lecture seule, sans greffon ni serveur MCP.

Tout est écrit hors du suivi git, dans `.chaine/revues/` :
- `journal.jsonl` : un évènement par début et fin d'agent, lu par `scripts/suivi.py`
- `<ticket>.json` et `<ticket>.md` : constats, verdicts, coût

Usage :
  python3 scripts/relecteurs.py                       relit chaque nouveau commit de ticket
  python3 scripts/relecteurs.py --ticket V0-01        relit un ticket déjà commité, puis sort
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

RACINE = Path(os.environ.get("CABINET_RACINE") or Path(__file__).resolve().parent.parent)
CHAINE = RACINE / ".chaine"
JOURNAL_BOUCLE = CHAINE / "journal.jsonl"
DOSSIER = CHAINE / "revues"
JOURNAL = DOSSIER / "journal.jsonl"
ARBRES = DOSSIER / "arbres"
FAITS = DOSSIER / "faits.json"
_VERROU = threading.Lock()

AGENTS = ("conformite", "principes", "tests", "code", "conduite")
NOMS = {
    "conformite": "Conformité",
    "principes": "Principes",
    "tests": "Tests de Qwen",
    "code": "Code",
    "conduite": "Conduite",
    "verificateur": "Vérificateur",
}

SCHEMA_CONSTATS = {
    "type": "object",
    "properties": {
        "verdict": {"enum": ["ok", "reserves", "ko"]},
        "resume": {"type": "string"},
        "constats": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "gravite": {"enum": ["bloquant", "majeur", "mineur", "remarque"]},
                    "titre": {"type": "string"},
                    "fichier": {"type": "string"},
                    "ligne": {"type": "integer"},
                    "regle": {"type": "string"},
                    "detail": {"type": "string"},
                    "attendu": {"type": "string"},
                },
                "required": ["gravite", "titre", "fichier", "regle", "detail"],
            },
        },
        "points_forts": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "resume", "constats", "points_forts"],
}

SCHEMA_VERIF = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "statut": {"enum": ["confirme", "rejete", "incertain"]},
                    "gravite": {"enum": ["bloquant", "majeur", "mineur", "remarque"]},
                    "raison": {"type": "string"},
                },
                "required": ["id", "statut", "gravite", "raison"],
            },
        },
        "verdict": {"enum": ["ok", "reserves", "ko"]},
        "synthese": {"type": "string"},
    },
    "required": ["decisions", "verdict", "synthese"],
}

COMMUN = """Tu es un relecteur automatique du projet Cabinet, angle « {nom} ». Tu travailles en
lecture seule : tu ne modifies, ne crées et ne lances rien d'autre que des commandes de lecture.

Le dossier courant est une copie figée du dépôt au commit {commit}, qui livre le ticket {ticket}.
Le travail à juger est `git diff {commit}^ {commit}` (vue d'ensemble : `git show --stat {commit}`).
Ce travail a été écrit par l'exécutant, un modèle local (Qwen), pendant une session non interactive.

Références qui font foi :
- `tickets/{ticket}.md`, puis les sections de `specs/` que le ticket cite ;
- `specs/00-vision-et-decisions.md` §5 (principes) et `specs/01-architecture.md` §2 et §5 ;
- `CLAUDE.md` et `docs/EXECUTANT.md` (règles de l'exécutant) ;
- les tests de contrat du ticket, `tests/contrats/test_{prefixe}_*.py`, écrits avant lui.
La vérification automatique de la boucle a réussi. Sa sortie est dans `.revue/make-check.txt`.
{extra}
Règles de ta relecture :
1. Ne rapporte que ce que tu as vu dans le code, avec le fichier, la ligne si possible, et la règle
   écrite qu'il enfreint (ticket, section de specs/, CLAUDE.md). Pas de constat de goût.
2. Gravité : « bloquant » enfreint un principe de specs/00 §5 ou un critère d'acceptation du ticket.
   « majeur » s'écarte de specs/ ou du ticket. « mineur » enfreint une convention. « remarque » sinon.
3. Les tests de contrat sont hors de ton jugement : ils ont été écrits par un autre relecteur.
4. Le verdict vaut « ko » s'il y a un constat bloquant, « reserves » s'il y a un majeur, « ok » sinon.
5. Réponds en français. Cite aussi deux ou trois points forts réels, s'il y en a.

Ton angle :
{angle}
"""

ANGLES = {
    "conformite": """Le code fait-il ce que le ticket demande, ni plus ni moins ?
- Reprends chaque critère d'acceptation et chaque point de « Contrat » et de « Précisions de la
  spécification » du ticket, un par un, et vérifie-le dans le code (pas seulement dans les tests).
- Vérifie que les noms (fonctions, classes, routes, champs, messages) sont ceux de specs/.
- Signale ce qui est fait en plus du ticket, et les fichiers modifiés hors de sa liste.""",
    "principes": """Les sept principes de specs/00 §5 et les contrôles transversaux de specs/09 §5.
- Aucune fonction d'envoi (smtplib, sendMail, send_message, appel d'envoi).
- Adresse du modèle limitée au bouclage. Aucun appel réseau dans le code ni dans les tests.
- Aucun accès à ~/Cabinet ni à un répertoire de données réel, aucune donnée réelle dans tests/.
- Seul clock.py lit l'heure du système. Seul config.py construit un chemin vers les données.
- Aucune dépendance hors de specs/00 §4.11. Aucune ressource externe dans web/templates ou static.
- Le texte lu reste une donnée (phrase de garde des prompts) quand le ticket touche au modèle.""",
    "tests": """Les tests écrits par Qwen dans tests/unit/ testent-ils vraiment le comportement ?
- Repère les tests complaisants : assertions vides ou toujours vraies, tests qui ne font que
  rejouer l'implémentation, cas d'erreur absents, doublons des tests de contrat sans valeur ajoutée.
- Vérifie qu'aucun test n'appelle le réseau ni un vrai modèle (httpx.MockTransport, doublures).
- Vérifie qu'aucun test ne lit ~/Cabinet et que CABINET_HOME pointe sur un dossier temporaire.
- Signale les cas du ticket qu'aucun test (unitaire ou de contrat) ne couvre.""",
    "code": """Conventions et lisibilité, au sens de specs/01 §5 et §7.
- Fonctions de plus de 40 lignes, annotations de type manquantes, état global hors configuration.
- Identifiants en anglais, messages d'erreur, textes d'interface et docstrings en français.
- Fichiers placés hors de l'arborescence de specs/01 §2.
- Code mort, duplication manifeste, gestion d'erreur qui avale une exception en silence.""",
    "conduite": """Comment Qwen a travaillé pendant sa session, d'après `.revue/session.md`
(chaque tour : sa pensée abrégée, les outils appelés, leurs résultats et les refus).
- Tentatives d'action interdites par docs/EXECUTANT.md : commit, modification de fichiers protégés,
  affaiblissement d'un test, lecture de scripts/ ou de chaine/, appel réseau.
- Contournements après un refus, hypothèses non vérifiées, décisions non notées dans
  tickets/notes/ alors que le ticket était ambigu.
- Temps perdu : longues réflexions sans action, relectures inutiles, boucles d'essais.
Le fichier à citer est `.revue/session.md`. Ici, « règle » désigne la règle de docs/EXECUTANT.md
ou de CLAUDE.md en cause.""",
}

VERIFICATEUR = """Tu es le vérificateur des relecteurs automatiques du projet Cabinet. Tu travailles en
lecture seule. Le dossier courant est une copie figée du dépôt au commit {commit} (ticket {ticket}).
Le travail jugé est `git diff {commit}^ {commit}`. Les références sont `tickets/{ticket}.md`, `specs/`,
`CLAUDE.md` et `docs/EXECUTANT.md`. La session de l'exécutant est résumée dans `.revue/session.md`.

Cinq relecteurs ont rendu les constats ci-dessous. Pour chacun, va voir le code et décide :
- « confirme » si le constat est exact et que la règle citée existe bien ;
- « rejete » s'il est faux, s'il ne cite pas de règle écrite, ou s'il juge les tests de contrat ;
- « incertain » si tu ne peux pas trancher.
Tu peux corriger la gravité. Garde l'identifiant de chaque constat.
Verdict : « ko » s'il reste un bloquant confirmé, « reserves » s'il reste un majeur confirmé, « ok » sinon.
La synthèse tient en trois phrases, en français.

Constats :
{constats}
"""


def maintenant() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def noter(evt: str, **champs: object) -> None:
    DOSSIER.mkdir(parents=True, exist_ok=True)
    ligne = json.dumps({"t": maintenant(), "evt": evt, **champs}, ensure_ascii=False)
    with _VERROU, JOURNAL.open("a", encoding="utf-8") as f:
        f.write(ligne + "\n")


def git(*args: str, cwd: Path = RACINE) -> str:
    res = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} : {res.stderr.strip()}")
    return res.stdout.strip()


def lire_jsonl(chemin: Path) -> list[dict]:
    if not chemin.exists():
        return []
    sortie = []
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        try:
            sortie.append(json.loads(ligne))
        except ValueError:
            continue
    return sortie


def commits_de_la_boucle() -> list[dict]:
    return [e for e in lire_jsonl(JOURNAL_BOUCLE) if e.get("evt") == "commit"]


def deja_faits() -> set[str]:
    try:
        return set(json.loads(FAITS.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def marquer_fait(cle: str) -> None:
    faits = deja_faits() | {cle}
    FAITS.write_text(json.dumps(sorted(faits)), encoding="utf-8")


# --------------------------------------------------------------------------
# Préparation de la copie figée


def resume_session(fichier: Path) -> str:
    """Résumé lisible de la session de l'exécutant, tour par tour."""
    if not fichier.exists():
        return "Session introuvable.\n"
    lignes = ["# Session de l'exécutant\n"]
    outils: dict[str, str] = {}
    tour_courant = None
    for brut in fichier.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            msg = json.loads(brut)
        except ValueError:
            continue
        if msg.get("type") == "assistant":
            message = msg.get("message") or {}
            if message.get("id") != tour_courant:
                tour_courant = message.get("id")
                lignes.append("\n## Tour\n")
            for bloc in message.get("content") or []:
                if bloc.get("type") == "thinking":
                    pensee = " ".join(bloc.get("thinking", "").split())
                    lignes.append(f"Pensée : {pensee[:700]}\n")
                elif bloc.get("type") == "text" and bloc.get("text", "").strip():
                    lignes.append(f"Texte : {bloc['text'].strip()[:500]}\n")
                elif bloc.get("type") == "tool_use":
                    entree = json.dumps(bloc.get("input") or {}, ensure_ascii=False)[:400]
                    outils[str(bloc.get("id"))] = bloc.get("name", "")
                    lignes.append(f"Outil {bloc.get('name')} : {entree}\n")
        elif msg.get("type") == "user":
            contenu = (msg.get("message") or {}).get("content")
            for bloc in contenu if isinstance(contenu, list) else []:
                if bloc.get("type") == "tool_result":
                    texte = bloc.get("content")
                    if isinstance(texte, list):
                        texte = " ".join(b.get("text", "") for b in texte if isinstance(b, dict))
                    etat = "REFUS OU ERREUR" if bloc.get("is_error") else "résultat"
                    lignes.append(f"  -> {etat} : {' '.join(str(texte).split())[:300]}\n")
        elif msg.get("type") == "result":
            lignes.append(f"\n## Fin\n{str(msg.get('result') or '')[:1500]}\n")
    return "".join(lignes)


def preparer_arbre(ticket: str, commit: str, tentative: int) -> Path:
    arbre = ARBRES / f"{ticket}-{commit}"
    if arbre.exists():
        subprocess.run(["git", "worktree", "remove", "--force", str(arbre)], cwd=RACINE,
                       capture_output=True, check=False)
        shutil.rmtree(arbre, ignore_errors=True)
    ARBRES.mkdir(parents=True, exist_ok=True)
    git("worktree", "add", "--detach", str(arbre), commit)
    revue = arbre / ".revue"
    revue.mkdir()
    check = CHAINE / "verifs" / f"{ticket}-t{tentative}-check.txt"
    texte = check.read_text(encoding="utf-8") if check.exists() else "Sortie introuvable.\n"
    (revue / "make-check.txt").write_text(texte, encoding="utf-8")
    session = CHAINE / "sessions" / f"{ticket}-t{tentative}.jsonl"
    (revue / "session.md").write_text(resume_session(session), encoding="utf-8")
    return arbre


def retirer_arbre(arbre: Path) -> None:
    subprocess.run(["git", "worktree", "remove", "--force", str(arbre)], cwd=RACINE,
                   capture_output=True, check=False)
    shutil.rmtree(arbre, ignore_errors=True)
    subprocess.run(["git", "worktree", "prune"], cwd=RACINE, capture_output=True, check=False)


# --------------------------------------------------------------------------
# Lancement d'un agent


def lancer_claude(consigne: str, schema: dict, arbre: Path, modele: str, budget: float) -> dict:
    commande = [
        "claude", "-p", consigne,
        "--model", modele,
        "--setting-sources", "project",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--tools", "Read,Grep,Glob,Bash",
        "--allowedTools", "Read", "Grep", "Glob",
        "Bash(git diff *)", "Bash(git show *)", "Bash(git log *)", "Bash(ls *)",
        "Bash(wc *)", "Bash(cat *)", "Bash(head *)", "Bash(grep *)",
        "--permission-prompts", "none",
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--max-budget-usd", str(budget),
    ]
    debut = time.monotonic()
    res = subprocess.run(commande, cwd=arbre, capture_output=True, text=True,
                         timeout=1800, check=False)
    duree = round(time.monotonic() - debut)
    try:
        sortie = json.loads(res.stdout)
    except ValueError:
        return {"erreur": (res.stderr or res.stdout)[-800:], "duree_s": duree, "cout": 0.0}
    resultat = sortie.get("structured_output")
    if resultat is None:
        try:
            resultat = json.loads(sortie.get("result") or "")
        except ValueError:
            resultat = None
    return {
        "resultat": resultat,
        "erreur": None if resultat else str(sortie.get("result") or "sortie vide")[:800],
        "duree_s": duree,
        "cout": round(float(sortie.get("total_cost_usd") or 0), 4),
        "tours": sortie.get("num_turns"),
    }


def relire_angle(agent: str, ticket: str, commit: str, arbre: Path, args) -> dict:
    noter("agent_debut", ticket=ticket, commit=commit, agent=agent)
    extra = ""
    if agent == "conduite":
        extra = "Le résumé de la session de l'exécutant est dans `.revue/session.md`.\n"
    consigne = COMMUN.format(
        nom=NOMS[agent], commit=commit, ticket=ticket, prefixe=ticket.lower().replace("-", "_"),
        extra=extra, angle=ANGLES[agent],
    )
    sortie = lancer_claude(consigne, SCHEMA_CONSTATS, arbre, args.modele, args.budget)
    if not sortie.get("resultat") and args.modele_verificateur != args.modele:
        # Un angle en erreur est rejoué une fois, sur le modèle du vérificateur.
        premier = sortie
        sortie = lancer_claude(consigne, SCHEMA_CONSTATS, arbre, args.modele_verificateur, args.budget)
        sortie["cout"] = sortie.get("cout", 0.0) + premier.get("cout", 0.0)
        sortie["duree_s"] = sortie.get("duree_s", 0) + premier.get("duree_s", 0)
    res = sortie.get("resultat") or {}
    constats = res.get("constats") or []
    for rang, c in enumerate(constats, 1):
        c["id"] = f"{agent}-{rang}"
        c["agent"] = agent
    noter(
        "agent_fin", ticket=ticket, commit=commit, agent=agent,
        verdict=res.get("verdict", "erreur"), constats=len(constats),
        gravites=compter(constats), cout=sortie["cout"], duree_s=sortie["duree_s"],
        erreur=sortie.get("erreur"),
    )
    return {"agent": agent, **sortie, "verdict": res.get("verdict", "erreur"),
            "resume": res.get("resume", ""), "constats": constats,
            "points_forts": res.get("points_forts", [])}


def compter(constats: list[dict]) -> dict:
    compte: dict[str, int] = {}
    for c in constats:
        compte[c.get("gravite", "remarque")] = compte.get(c.get("gravite", "remarque"), 0) + 1
    return compte


def verifier(ticket: str, commit: str, arbre: Path, angles: list[dict], args) -> dict:
    tous = [c for a in angles for c in a["constats"]]
    noter("agent_debut", ticket=ticket, commit=commit, agent="verificateur", a_verifier=len(tous))
    if not tous:
        noter("agent_fin", ticket=ticket, commit=commit, agent="verificateur", verdict="ok",
              constats=0, confirmes=0, rejetes=0, cout=0.0, duree_s=0)
        return {"agent": "verificateur", "verdict": "ok", "decisions": [], "cout": 0.0,
                "synthese": "Aucun constat à vérifier.", "duree_s": 0}
    texte = json.dumps(
        [{k: c.get(k) for k in ("id", "agent", "gravite", "titre", "fichier", "ligne",
                                "regle", "detail", "attendu")} for c in tous],
        ensure_ascii=False, indent=1,
    )
    consigne = VERIFICATEUR.format(commit=commit, ticket=ticket, constats=texte)
    sortie = lancer_claude(consigne, SCHEMA_VERIF, arbre, args.modele_verificateur, args.budget)
    res = sortie.get("resultat") or {}
    decisions = {d["id"]: d for d in res.get("decisions") or []}
    for c in tous:
        d = decisions.get(c["id"])
        c["statut"] = d["statut"] if d else "incertain"
        c["raison"] = d["raison"] if d else "non examiné par le vérificateur"
        if d:
            c["gravite"] = d.get("gravite", c["gravite"])
    confirmes = [c for c in tous if c["statut"] == "confirme"]
    noter(
        "agent_fin", ticket=ticket, commit=commit, agent="verificateur",
        verdict=res.get("verdict", "erreur"), constats=len(tous), confirmes=len(confirmes),
        rejetes=sum(1 for c in tous if c["statut"] == "rejete"),
        gravites=compter(confirmes), cout=sortie["cout"], duree_s=sortie["duree_s"],
        erreur=sortie.get("erreur"),
    )
    return {"agent": "verificateur", **sortie, "verdict": res.get("verdict", "erreur"),
            "synthese": res.get("synthese", "")}


# --------------------------------------------------------------------------
# Relecture d'un ticket


def relire(ticket: str, commit: str, tentative: int, args) -> dict:
    debut = time.monotonic()
    noter("revue_debut", ticket=ticket, commit=commit, agents=[*AGENTS, "verificateur"],
          modele=args.modele)
    arbre = preparer_arbre(ticket, commit, tentative)
    try:
        with ThreadPoolExecutor(max_workers=len(AGENTS)) as pool:
            angles = list(pool.map(lambda a: relire_angle(a, ticket, commit, arbre, args), AGENTS))
        verif = verifier(ticket, commit, arbre, angles, args)
    finally:
        retirer_arbre(arbre)
    constats = [c for a in angles for c in a["constats"]]
    confirmes = [c for c in constats if c.get("statut") == "confirme"]
    cout = round(sum(a.get("cout", 0) for a in angles) + verif.get("cout", 0), 4)
    # Un angle qui n'a pas pu relire laisse un trou : le ticket ne peut pas sortir « ok ».
    incomplets = [a["agent"] for a in angles if a["verdict"] == "erreur"]
    verdict, synthese = verif["verdict"], verif.get("synthese", "")
    if incomplets:
        verdict = "reserves" if verdict == "ok" else verdict
        synthese = ("Relecture incomplète, angle en erreur : "
                    + ", ".join(NOMS[a] for a in incomplets) + ". " + synthese)
    rapport = {
        "ticket": ticket, "commit": commit, "tentative": tentative, "date": maintenant(),
        "verdict": verdict, "synthese": synthese, "incomplets": incomplets,
        "cout_usd": cout, "duree_s": round(time.monotonic() - debut),
        "modeles": {"angles": args.modele, "verificateur": args.modele_verificateur},
        "angles": [{k: a.get(k) for k in ("agent", "verdict", "resume", "points_forts",
                                          "cout", "duree_s", "erreur")} for a in angles],
        "constats": constats,
    }
    (DOSSIER / f"{ticket}.json").write_text(json.dumps(rapport, ensure_ascii=False, indent=1),
                                            encoding="utf-8")
    (DOSSIER / f"{ticket}.md").write_text(rapport_markdown(rapport), encoding="utf-8")
    noter("revue_fin", ticket=ticket, commit=commit, verdict=rapport["verdict"],
          constats=len(constats), confirmes=len(confirmes), gravites=compter(confirmes),
          cout=cout, duree_s=rapport["duree_s"])
    return rapport


def rapport_markdown(r: dict) -> str:
    lignes = [
        f"# Relecture automatique de {r['ticket']}\n",
        f"Commit `{r['commit']}`, relu le {r['date'][:16].replace('T', ' à ')}. "
        f"Verdict : **{r['verdict']}**. Coût : {r['cout_usd']} $.\n",
        f"{r['synthese']}\n",
        "## Angles\n",
        "| Agent | Verdict | Résumé |",
        "| --- | --- | --- |",
    ]
    for a in r["angles"]:
        resume = (a.get("resume") or a.get("erreur") or "").replace("\n", " ").replace("|", "/")
        lignes.append(f"| {NOMS[a['agent']]} | {a['verdict']} | {resume} |")
    lignes += ["", "## Constats\n", "| Statut | Gravité | Agent | Fichier | Constat | Règle |",
               "| --- | --- | --- | --- | --- | --- |"]
    for c in sorted(r["constats"], key=lambda c: (c.get("statut") != "confirme", c["gravite"])):
        lieu = c.get("fichier", "") + (f":{c['ligne']}" if c.get("ligne") else "")
        titre = c["titre"].replace("|", "/")
        lignes.append(f"| {c.get('statut', '')} | {c['gravite']} | {NOMS[c['agent']]} | "
                      f"`{lieu}` | {titre} | {c['regle'].replace('|', '/')} |")
    return "\n".join(lignes) + "\n"


# --------------------------------------------------------------------------
# Programme principal


def veiller(args) -> int:
    DOSSIER.mkdir(parents=True, exist_ok=True)
    (DOSSIER / "veille.pid").write_text(str(os.getpid()), encoding="utf-8")
    print(f"Relecteurs en veille sur {JOURNAL_BOUCLE} (modèle {args.modele}).", flush=True)
    noter("veille_debut", modele=args.modele, verificateur=args.modele_verificateur)
    while True:
        for e in commits_de_la_boucle():
            cle = f"{e['ticket']}@{e['commit']}"
            if cle in deja_faits():
                continue
            try:
                relire(e["ticket"], e["commit"], int(e.get("tentative") or 1), args)
            except Exception as erreur:  # noqa: BLE001  une relecture ratée ne doit pas arrêter la veille
                noter("revue_erreur", ticket=e["ticket"], commit=e["commit"], erreur=str(erreur)[:500])
            marquer_fait(cle)
        time.sleep(15)


def main() -> int:
    analyseur = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    analyseur.add_argument("--ticket", help="relit ce ticket déjà commité, puis sort")
    analyseur.add_argument("--modele", default="claude-sonnet-5-5")
    analyseur.add_argument("--modele-verificateur", default="claude-opus-5-5")
    analyseur.add_argument("--budget", type=float, default=2.0, help="plafond en dollars par agent")
    args = analyseur.parse_args()
    if shutil.which("claude") is None:
        print("La commande claude est introuvable.", file=sys.stderr)
        return 2
    if not args.ticket:
        return veiller(args)
    commit = next((e for e in reversed(commits_de_la_boucle()) if e["ticket"] == args.ticket), None)
    if commit is None:
        print(f"Aucun commit de {args.ticket} dans {JOURNAL_BOUCLE}.", file=sys.stderr)
        return 2
    rapport = relire(args.ticket, commit["commit"], int(commit.get("tentative") or 1), args)
    marquer_fait(f"{args.ticket}@{commit['commit']}")
    print(rapport_markdown(rapport))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
