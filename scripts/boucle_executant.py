#!/usr/bin/env python3
"""Boucle de l'exécutant : une session Claude Code non interactive par ticket.

Pour chaque ticket « à faire » de la vague, dans l'ordre de `tickets/ETAT.md` :

1. lance une session `claude -p` reliée au modèle local par la passerelle ;
2. restaure tout fichier protégé que la session aurait touché ;
3. lance elle-même `make install` si besoin, puis `make check` ;
4. si la vérification passe, met `tickets/ETAT.md` à jour et fait le commit ;
5. sinon relance une session avec la sortie de l'échec, trois tentatives au plus ;
6. après trois échecs, met de côté les modifications, écrit une note et marque
   le ticket « bloqué ».

Le script n'écrit jamais de code d'application. Il ne dépend que de la
bibliothèque standard. Voir `docs/REPRISE.md`.

Usage :
    python3 scripts/boucle_executant.py 0
    python3 scripts/boucle_executant.py 0 --ticket V0-02
    python3 scripts/boucle_executant.py --intervention "ce qui a été fait"
    python3 scripts/boucle_executant.py --remettre V0-02
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
DOSSIER = RACINE / ".chaine"
JOURNAL = DOSSIER / "journal.jsonl"
SESSIONS = DOSSIER / "sessions"
VERIFS = DOSSIER / "verifs"
FICHIER_PID = DOSSIER / "boucle.pid"
ETAT = RACINE / "tickets" / "ETAT.md"
ETAT_REL = "tickets/ETAT.md"

MAX_TENTATIVES = 3
PYTHON_MINIMAL = (3, 12)
PORT_MODELE = 8080
PORT_PASSERELLE = 4000
RE_ID = re.compile(r"V\d+-(?:\d+|C\d+)")

# Chemins que l'exécutant ne doit jamais modifier. Le script les restaure
# après chaque session et note l'écart dans le journal.
PROTEGES = (
    "tests/contrats/",
    "specs/",
    "docs/",
    "scripts/",
    "chaine/",
    "revues/",
    "SPEC.md",
    "REVIEW.md",
    "CLAUDE.md",
    "tickets/ETAT.md",
)
RE_TICKET_PROTEGE = re.compile(r"^tickets/V\d+-(?:\d+|C\d+)\.md$")

OUTILS = "Bash,Read,Edit,Write,Glob,Grep"
AUTORISES = [
    "Read",
    "Edit",
    "Write",
    "Glob",
    "Grep",
    "Bash(make)",
    "Bash(make *)",
    "Bash(.venv/bin/*)",
    "Bash(ls)",
    "Bash(ls *)",
    "Bash(pwd)",
    "Bash(cat *)",
    "Bash(head *)",
    "Bash(tail *)",
    "Bash(grep *)",
    "Bash(find *)",
    "Bash(wc *)",
    "Bash(mkdir *)",
    "Bash(touch *)",
    "Bash(git status)",
    "Bash(git status *)",
    "Bash(git diff)",
    "Bash(git diff *)",
    "Bash(git log *)",
    "Bash(git ls-files)",
    "Bash(git ls-files *)",
    "Bash(git check-ignore *)",
]
INTERDITS = [
    "Edit(tests/contrats/**)",
    "Edit(specs/**)",
    "Edit(docs/**)",
    "Edit(scripts/**)",
    "Edit(chaine/**)",
    "Edit(revues/**)",
    "Edit(SPEC.md)",
    "Edit(REVIEW.md)",
    "Edit(CLAUDE.md)",
    "Edit(tickets/V*.md)",
    "Edit(tickets/ETAT.md)",
    "Bash(git commit *)",
    "Bash(git push *)",
    "Bash(git reset *)",
    "Bash(git checkout *)",
    "Bash(git stash *)",
    # Fichiers de la chaîne : inutiles à l'exécutant et coûteux en contexte.
    "Read(scripts/**)",
    "Read(chaine/**)",
    "Read(revues/**)",
    "Read(.chaine/**)",
]

_session_en_cours: subprocess.Popen | None = None


# --------------------------------------------------------------------------
# Journal


def maintenant() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def noter(evt: str, **champs: object) -> None:
    """Ajoute une ligne au journal de la chaîne (JSON, une ligne par évènement)."""
    DOSSIER.mkdir(exist_ok=True)
    ligne = {"t": maintenant(), "evt": evt, **champs}
    with JOURNAL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(ligne, ensure_ascii=False) + "\n")
    visibles = {k: v for k, v in champs.items() if k not in ("sortie", "detail")}
    print(f"[{ligne['t'][11:19]}] {evt} {json.dumps(visibles, ensure_ascii=False)}", flush=True)


# --------------------------------------------------------------------------
# Git


def git(*args: str, verifier: bool = True) -> str:
    res = subprocess.run(
        ["git", *args], cwd=RACINE, capture_output=True, text=True, check=False
    )
    if verifier and res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} : {res.stderr.strip()}")
    return res.stdout.strip()


def chemins_modifies() -> list[str]:
    """Chemins modifiés, ajoutés ou supprimés par rapport au dernier commit."""
    brut = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=RACINE,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    chemins: list[str] = []
    morceaux = brut.split("\0")
    i = 0
    while i < len(morceaux):
        entree = morceaux[i]
        i += 1
        if len(entree) < 4:
            continue
        code, chemin = entree[:2], entree[3:]
        chemins.append(chemin)
        if code[0] in "RC":  # renommage : l'ancien chemin suit
            if i < len(morceaux) and morceaux[i]:
                chemins.append(morceaux[i])
            i += 1
    return sorted(set(chemins))


def est_protege(chemin: str) -> bool:
    if RE_TICKET_PROTEGE.match(chemin):
        return True
    return any(
        chemin == p or (p.endswith("/") and chemin.startswith(p)) for p in PROTEGES
    )


def restaurer_proteges(etat_attendu: str) -> list[str]:
    """Remet les fichiers protégés dans l'état du dernier commit.

    `tickets/ETAT.md` est tenu par la boucle : il est comparé au contenu
    qu'elle avait écrit avant la session, pas au dernier commit.
    """
    git("reset", "-q", verifier=False)
    touches = [c for c in chemins_modifies() if est_protege(c) and c != ETAT_REL]
    if not ETAT.exists() or ETAT.read_text(encoding="utf-8") != etat_attendu:
        ETAT.write_text(etat_attendu, encoding="utf-8")
        touches.append(ETAT_REL)
    for chemin in touches:
        if chemin == ETAT_REL:
            continue
        suivi = (
            subprocess.run(
                ["git", "cat-file", "-e", f"HEAD:{chemin}"],
                cwd=RACINE,
                capture_output=True,
                check=False,
            ).returncode
            == 0
        )
        if suivi:
            git("checkout", "HEAD", "--", chemin)
        else:
            cible = RACINE / chemin
            if cible.is_file() or cible.is_symlink():
                cible.unlink()
    return touches


def preparer_exclusions() -> None:
    """Ignore localement les fichiers de la chaîne, sans toucher au dépôt."""
    exclure = RACINE / ".git" / "info" / "exclude"
    exclure.parent.mkdir(parents=True, exist_ok=True)
    contenu = exclure.read_text(encoding="utf-8") if exclure.exists() else ""
    ajouts = [m for m in (".chaine/", ".venv/") if m not in contenu.splitlines()]
    if ajouts:
        with exclure.open("a", encoding="utf-8") as f:
            f.write("\n".join(["", *ajouts, ""]))


# --------------------------------------------------------------------------
# Tableau d'état


def lire_etat() -> tuple[list[str], dict[str, dict]]:
    lignes = ETAT.read_text(encoding="utf-8").splitlines()
    tickets: dict[str, dict] = {}
    for i, ligne in enumerate(lignes):
        cellules = [c.strip() for c in ligne.strip().strip("|").split("|")]
        if len(cellules) != 6 or not RE_ID.fullmatch(cellules[0]):
            continue
        tickets[cellules[0]] = {
            "ligne": i,
            "id": cellules[0],
            "titre": cellules[1],
            "depend": RE_ID.findall(cellules[2]),
            "depend_brut": cellules[2],
            "etat": cellules[3],
            "tentatives": int(cellules[4] or 0),
            "commit": cellules[5],
        }
    return lignes, tickets


def ecrire_ticket(ident: str, **changements: object) -> None:
    lignes, tickets = lire_etat()
    t = {**tickets[ident], **changements}
    lignes[t["ligne"]] = (
        f"| {t['id']} | {t['titre']} | {t['depend_brut']} | {t['etat']} "
        f"| {t['tentatives']} | {t['commit']} |"
    )
    ETAT.write_text("\n".join(lignes) + "\n", encoding="utf-8")


def fichiers_du_ticket(ident: str) -> list[str]:
    """Liste « Fichiers à créer ou modifier » du ticket."""
    texte = (RACINE / "tickets" / f"{ident}.md").read_text(encoding="utf-8")
    bloc = re.search(r"## Fichiers à créer ou modifier\n(.*?)(?:\n## |\Z)", texte, re.DOTALL)
    return re.findall(r"^- `([^`]+)`", bloc.group(1), re.MULTILINE) if bloc else []


# --------------------------------------------------------------------------
# Prérequis


def port_ouvert(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(2)
        return s.connect_ex(("127.0.0.1", port)) == 0


def verifier_prerequis(vague: int, factice: bool) -> list[str]:
    manques: list[str] = []
    if sys.version_info[:2] < PYTHON_MINIMAL:
        manques.append(f"python3 est en {sys.version.split()[0]}, il faut 3.12 ou plus")
    if not git("config", "user.name", verifier=False) or not git(
        "config", "user.email", verifier=False
    ):
        manques.append('identité git absente : git config user.name / user.email')
    branche = git("branch", "--show-current")
    if branche != f"vague-{vague}":
        manques.append(f"branche courante « {branche} », attendu « vague-{vague} »")
    if not git("tag", "--list", f"vague-{vague}-tests"):
        manques.append(
            f"étiquette vague-{vague}-tests absente : la phase A du relecteur n'est pas faite"
        )
    if not factice:
        if shutil.which("claude") is None:
            manques.append("commande claude introuvable")
        if not port_ouvert(PORT_MODELE):
            manques.append(
                f"serveur du modèle muet sur 127.0.0.1:{PORT_MODELE} : scripts/chaine.sh demarrer"
            )
        if not port_ouvert(PORT_PASSERELLE):
            manques.append(
                f"passerelle muette sur 127.0.0.1:{PORT_PASSERELLE} : scripts/chaine.sh demarrer"
            )
    return manques


# --------------------------------------------------------------------------
# Session de l'exécutant


def environnement(ticket: str, modele: str | None = None) -> dict[str, str]:
    """Environnement sans aucune variable du relecteur, données d'essai isolées."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("ANTHROPIC", "CLAUDE", "MAKE", "MFLAGS"))
    }
    env["CABINET_TICKET"] = ticket
    env["CABINET_HOME"] = str(DOSSIER / "cabinet-home-essai")
    env.pop("CABINET_ALLOW_REMOTE_LLM", None)
    env.pop("CABINET_CONTRATS", None)
    if modele:
        env.update(
            {
                "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{PORT_PASSERELLE}",
                "ANTHROPIC_AUTH_TOKEN": cle_passerelle(),
                "ANTHROPIC_MODEL": modele,
                "ANTHROPIC_SMALL_FAST_MODEL": modele,
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": modele,
                "ANTHROPIC_DEFAULT_SONNET_MODEL": modele,
                "ANTHROPIC_DEFAULT_OPUS_MODEL": modele,
                "CLAUDE_CODE_SUBAGENT_MODEL": modele,
                "CLAUDE_CONFIG_DIR": str(
                    Path.home() / ".cabinet-chaine" / "claude-executant"
                ),
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "DISABLE_TELEMETRY": "1",
                "DISABLE_AUTOUPDATER": "1",
                # Qwen3.8 raisonne longuement avant d'agir : un plafond bas
                # coupe le tour avant le premier appel d'outil.
                "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "32000",
                "API_TIMEOUT_MS": "900000",
            }
        )
    return env


def cle_passerelle() -> str:
    """Clé locale de la passerelle, créée par `scripts/chaine.sh`, hors du dépôt."""
    fichier = Path.home() / ".cabinet-chaine" / "cle-passerelle"
    return fichier.read_text().strip() if fichier.exists() else "jeton-factice-local"


def consigne(ticket: dict, tentative: int, echec: str | None) -> str:
    ident = ticket["id"]
    motif = "test_" + ident.lower().replace("-", "_") + "_*.py"
    texte = (
        "Tu es l'exécutant. Lis `CLAUDE.md` puis `docs/EXECUTANT.md`.\n"
        f"Traite un seul ticket : {ident}, « {ticket['titre']} ». "
        f"Il est dans `tickets/{ident}.md`. "
        f"Ses tests de contrat sont dans `tests/contrats/{motif}`. Lis-les, "
        "ils disent ce qui sera vérifié. Tu ne peux pas les modifier.\n"
        "Écris le code dans les fichiers listés par le ticket et tes propres "
        "tests dans `tests/unit/`. Lance ensuite `make check` et corrige "
        "jusqu'à ce qu'il passe. `make check` doit aussi exécuter les tests "
        "de contrat du ticket.\n"
        "Tu n'as pas à lire `scripts/`, `chaine/`, `revues/` ni les autres "
        "tickets : ils ne te concernent pas. Avance fichier par fichier, "
        "sans tout planifier d'avance.\n"
        "Ne fais pas de commit et ne modifie pas `tickets/ETAT.md` : le script "
        "qui t'a lancé relance `make check` lui-même, fait le commit et tient "
        "l'état. Quand `make check` passe, arrête-toi et résume en cinq lignes "
        "ce que tu as fait."
    )
    if echec:
        texte += (
            f"\n\nC'est la tentative {tentative} sur {MAX_TENTATIVES}. Tes "
            "modifications de la tentative précédente sont encore dans l'arbre "
            "de travail. La vérification faite par le script a échoué.\n"
            f"{echec}"
        )
    return texte


def resumer_outil(bloc: dict) -> str:
    entree = bloc.get("input") or {}
    for cle in ("command", "file_path", "pattern", "path"):
        if cle in entree:
            return str(entree[cle])[:200]
    return json.dumps(entree, ensure_ascii=False)[:200]


def suivre_sortie(flux, fichier: Path, ticket: str, tentative: int, bilan: dict) -> None:
    """Recopie la sortie de la session et en tire les évènements du journal."""
    with fichier.open("w", encoding="utf-8") as f:
        for ligne in flux:
            f.write(ligne)
            f.flush()
            try:
                msg = json.loads(ligne)
            except ValueError:
                continue
            genre = msg.get("type")
            if genre == "system" and msg.get("subtype") == "init":
                noter(
                    "session_init",
                    ticket=ticket,
                    tentative=tentative,
                    modele=msg.get("model"),
                    outils=msg.get("tools"),
                    serveurs_mcp=len(msg.get("mcp_servers") or []),
                )
            elif genre == "assistant":
                for bloc in (msg.get("message") or {}).get("content") or []:
                    if bloc.get("type") == "tool_use":
                        bilan["appels_outils"] += 1
                        noter(
                            "outil",
                            ticket=ticket,
                            tentative=tentative,
                            nom=bloc.get("name"),
                            resume=resumer_outil(bloc),
                        )
                    elif bloc.get("type") == "text" and bloc.get("text", "").strip():
                        noter(
                            "texte",
                            ticket=ticket,
                            tentative=tentative,
                            resume=bloc["text"].strip()[:400],
                        )
            elif genre == "user":
                contenu = (msg.get("message") or {}).get("content")
                for bloc in contenu if isinstance(contenu, list) else []:
                    if bloc.get("type") == "tool_result" and bloc.get("is_error"):
                        bilan["erreurs_outils"] += 1
                        noter(
                            "outil_erreur",
                            ticket=ticket,
                            tentative=tentative,
                            resume=str(bloc.get("content"))[:300],
                        )
            elif genre == "result":
                bilan["resultat"] = msg


def lancer_session(ticket: dict, tentative: int, echec: str | None, args) -> dict:
    """Lance une session et renvoie son bilan."""
    global _session_en_cours
    ident = ticket["id"]
    SESSIONS.mkdir(parents=True, exist_ok=True)
    fichier = SESSIONS / f"{ident}-t{tentative}.jsonl"
    erreurs = SESSIONS / f"{ident}-t{tentative}.stderr.txt"
    texte = consigne(ticket, tentative, echec)
    if args.executant_factice:
        commande = ["/bin/sh", "-c", args.executant_factice]
    else:
        commande = [
            "claude",
            "-p",
            texte,
            "--model",
            args.modele,
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "acceptEdits",
            "--permission-prompts",
            "none",
            "--tools",
            OUTILS,
            "--allowedTools",
            *AUTORISES,
            "--disallowedTools",
            *INTERDITS,
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--autocompact",
            "100k",
        ]
    bilan: dict = {"appels_outils": 0, "erreurs_outils": 0, "resultat": None}
    debut = time.monotonic()
    noter("session_debut", ticket=ident, tentative=tentative, fichier=str(fichier))
    with erreurs.open("w", encoding="utf-8") as ferr:
        proc = subprocess.Popen(
            commande,
            cwd=RACINE,
            env=environnement(ident, None if args.executant_factice else args.modele),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=ferr,
            text=True,
            start_new_session=True,
        )
        _session_en_cours = proc
        lecteur = threading.Thread(
            target=suivre_sortie,
            args=(proc.stdout, fichier, ident, tentative, bilan),
            daemon=True,
        )
        lecteur.start()
        delai_depasse = False
        try:
            proc.wait(timeout=args.delai_session * 60)
        except subprocess.TimeoutExpired:
            delai_depasse = True
            arreter_groupe(proc)
        lecteur.join(timeout=10)
        _session_en_cours = None
    resultat = bilan["resultat"] or {}
    usage = resultat.get("usage") or {}
    fin = {
        "code": proc.returncode,
        "delai_depasse": delai_depasse,
        "duree_s": round(time.monotonic() - debut),
        "tours": resultat.get("num_turns"),
        "appels_outils": bilan["appels_outils"],
        "erreurs_outils": bilan["erreurs_outils"],
        "refus_permission": len(resultat.get("permission_denials") or []),
        "tokens_entree": usage.get("input_tokens"),
        "tokens_sortie": usage.get("output_tokens"),
        "erreur_session": bool(resultat.get("is_error")),
        "resume": str(resultat.get("result") or "")[:600],
    }
    noter("session_fin", ticket=ident, tentative=tentative, **fin)
    return fin


def arreter_groupe(proc: subprocess.Popen) -> None:
    for signal_envoye in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, signal_envoye)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=15)
            return
        except subprocess.TimeoutExpired:
            continue


# --------------------------------------------------------------------------
# Vérification


def lancer_make(cible: str, ticket: str, delai_s: int, extra: dict | None = None) -> tuple[int, str]:
    env = environnement(ticket)
    env.update(extra or {})
    try:
        res = subprocess.run(
            ["make", cible],
            cwd=RACINE,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=delai_s,
            check=False,
        )
        return res.returncode, res.stdout
    except subprocess.TimeoutExpired as exc:
        sortie = exc.stdout or ""
        if isinstance(sortie, bytes):
            sortie = sortie.decode("utf-8", "replace")
        return 124, sortie + f"\n[délai de {delai_s} s dépassé]"


def empreinte_installation() -> str:
    h = hashlib.sha256()
    for nom in ("pyproject.toml", "Makefile"):
        chemin = RACINE / nom
        h.update(chemin.read_bytes() if chemin.exists() else b"-")
    return h.hexdigest()


def fin_de_sortie(sortie: str, lignes: int = 60) -> str:
    return "\n".join(sortie.strip().splitlines()[-lignes:])


def verifier(ticket: dict, tentative: int, etat_attendu: str) -> tuple[bool, str]:
    """Vérifie le travail de la session. Renvoie (succès, message pour l'exécutant)."""
    ident = ticket["id"]
    VERIFS.mkdir(parents=True, exist_ok=True)
    base = VERIFS / f"{ident}-t{tentative}"

    touches = restaurer_proteges(etat_attendu)
    if touches:
        noter("garde", ticket=ident, tentative=tentative, restaures=touches)

    modifies = [c for c in chemins_modifies() if c != ETAT_REL]
    if not modifies:
        noter("verif", ticket=ident, tentative=tentative, ok=False, motif="aucune modification")
        return False, "Aucun fichier n'a été créé ni modifié par la session."

    attendus = set(fichiers_du_ticket(ident))
    hors_liste = [
        c for c in modifies if c not in attendus and not c.startswith("tickets/notes/")
    ]
    if hors_liste:
        noter("hors_liste", ticket=ident, tentative=tentative, fichiers=hors_liste)

    if not (RACINE / "Makefile").exists():
        noter("verif", ticket=ident, tentative=tentative, ok=False, motif="Makefile absent")
        return False, "Le fichier `Makefile` n'existe pas, `make check` ne peut pas tourner."

    marque = DOSSIER / "installation.sha256"
    empreinte = empreinte_installation()
    deja = marque.read_text().strip() if marque.exists() else ""
    if not (RACINE / ".venv").exists() or deja != empreinte:
        code, sortie = lancer_make("install", ident, 1200)
        Path(f"{base}-install.txt").write_text(sortie, encoding="utf-8")
        noter("install", ticket=ident, tentative=tentative, code=code)
        if code != 0:
            noter("verif", ticket=ident, tentative=tentative, ok=False, motif="make install échoue")
            return False, (
                f"`make install` sort avec le code {code}. Fin de la sortie :\n"
                f"```\n{fin_de_sortie(sortie)}\n```"
            )
        marque.write_text(empreinte + "\n")

    rapport = Path(f"{base}-contrats.json")
    rapport.unlink(missing_ok=True)
    maison = Path.home() / "Cabinet"
    maison_avant = maison.exists()
    code, sortie = lancer_make(
        "check", ident, 900, {"CABINET_RAPPORT_CONTRATS": str(rapport)}
    )
    Path(f"{base}-check.txt").write_text(sortie, encoding="utf-8")
    if maison.exists() and not maison_avant:
        noter("alerte", ticket=ident, detail="~/Cabinet a été créé pendant make check")

    contrats: dict = {}
    if rapport.exists():
        try:
            contrats = json.loads(rapport.read_text(encoding="utf-8")).get(ident, {})
        except ValueError:
            contrats = {}
    if code != 0:
        motif, message = "make check échoue", (
            f"`make check` sort avec le code {code}. Fin de la sortie :\n"
            f"```\n{fin_de_sortie(sortie)}\n```"
        )
    elif (
        not contrats.get("reussis")
        or contrats.get("echoues")
        or contrats.get("erreurs_collecte")
    ):
        motif, message = "contrats non exécutés", (
            "`make check` sort avec le code 0, mais les tests de contrat du "
            f"ticket n'ont pas tous été exécutés et réussis : {contrats}. "
            "`make check` doit lancer pytest sur tout le dossier `tests/`, "
            "y compris `tests/contrats/`."
        )
    elif contrats.get("ignores"):
        motif, message = "contrats ignorés", (
            f"Des tests de contrat du ticket ont été ignorés : {contrats}."
        )
    else:
        motif, message = "", ""
    noter(
        "verif",
        ticket=ident,
        tentative=tentative,
        ok=not motif,
        code=code,
        motif=motif,
        contrats=contrats,
    )
    return not motif, message


# --------------------------------------------------------------------------
# Issue d'un ticket


def valider(ticket: dict, tentative: int) -> None:
    ident = ticket["id"]
    ecrire_ticket(ident, etat="fait", tentatives=tentative)
    git("add", "-A")
    git("commit", "-q", "-m", f"[{ident}] {ticket['titre']}")
    empreinte = git("rev-parse", "--short", "HEAD")
    ecrire_ticket(ident, commit=empreinte)
    git("add", "tickets/ETAT.md")
    git("commit", "-q", "-m", f"[V{ident[1:].split('-')[0]}-etat] {ident} fait")
    noter("commit", ticket=ident, tentative=tentative, commit=empreinte)


def bloquer(ticket: dict, tentative: int, echecs: list[str]) -> None:
    ident = ticket["id"]
    reserve = ""
    if chemins_modifies():
        git("stash", "push", "--include-untracked", "-q", "-m", f"échec {ident}")
        reserve = git("stash", "list", "--format=%gd %s", "-1")
    notes = RACINE / "tickets" / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    corps = [
        f"# {ident} · bloqué",
        "",
        (
            f"Note écrite par `scripts/boucle_executant.py` le {maintenant()}, après "
            f"{tentative} tentatives. Elle rapporte des faits, pas une analyse."
        ),
        "",
    ]
    if reserve:
        corps += [f"Modifications mises de côté : `{reserve}` (voir `git stash list`).", ""]
    for n, texte in enumerate(echecs, start=1):
        corps += [f"## Tentative {n}", "", texte, ""]
    (notes / f"{ident}.md").write_text("\n".join(corps), encoding="utf-8")
    ecrire_ticket(ident, etat="bloqué", tentatives=tentative)
    git("add", "tickets/ETAT.md", f"tickets/notes/{ident}.md")
    git("commit", "-q", "-m", f"[V{ident[1:].split('-')[0]}-etat] {ident} bloqué")
    noter("bloque", ticket=ident, tentatives=tentative, reserve=reserve)


def traiter(ticket: dict, args) -> None:
    ident = ticket["id"]
    echecs: list[str] = []
    tentative = ticket["tentatives"]
    echec: str | None = None
    while tentative < MAX_TENTATIVES:
        tentative += 1
        ecrire_ticket(ident, tentatives=tentative)
        etat_attendu = ETAT.read_text(encoding="utf-8")
        bilan = lancer_session(ticket, tentative, echec, args)
        ok, message = verifier(ticket, tentative, etat_attendu)
        if ok:
            valider(ticket, tentative)
            return
        if bilan["delai_depasse"]:
            message = f"La session a dépassé le délai de {args.delai_session} minutes.\n" + message
        echec = message
        echecs.append(message)
    bloquer(ticket, tentative, echecs)


# --------------------------------------------------------------------------
# Programme principal


def a_traiter(vague: int, seul: str | None) -> tuple[list[dict], list[dict]]:
    _, tickets = lire_etat()
    prets, sautes = [], []
    for t in tickets.values():
        if not t["id"].startswith(f"V{vague}-") or t["etat"] != "à faire":
            continue
        if seul and t["id"] != seul:
            continue
        dependances = [tickets[d]["etat"] for d in t["depend"] if d in tickets]
        (prets if all(e == "fait" for e in dependances) else sautes).append(t)
    return prets, sautes


def interrompre(signum, _cadre) -> None:
    if _session_en_cours is not None:
        arreter_groupe(_session_en_cours)
    noter("boucle_arret", signal=signum)
    FICHIER_PID.unlink(missing_ok=True)
    sys.exit(130)


def boucle(args) -> int:
    vague = args.vague
    DOSSIER.mkdir(exist_ok=True)
    preparer_exclusions()
    if FICHIER_PID.exists():
        try:
            os.kill(int(FICHIER_PID.read_text()), 0)
            print("Une boucle tourne déjà (.chaine/boucle.pid).", file=sys.stderr)
            return 2
        except (ProcessLookupError, ValueError):
            FICHIER_PID.unlink()
    manques = verifier_prerequis(vague, bool(args.executant_factice))
    if manques:
        print("Prérequis manquants :", file=sys.stderr)
        for m in manques:
            print(f"  - {m}", file=sys.stderr)
        return 2
    if chemins_modifies() and not args.garder:
        if not args.nettoyer:
            print(
                "L'arbre de travail n'est pas propre. Fais un commit, ou relance "
                "avec --nettoyer (met ces modifications de côté par git stash) "
                "ou --garder (reprend le ticket avec ces modifications).",
                file=sys.stderr,
            )
            return 2
        git("stash", "push", "--include-untracked", "-q", "-m", "avant reprise de la boucle")
        noter("nettoyage", reserve=git("stash", "list", "--format=%gd %s", "-1"))

    FICHIER_PID.write_text(str(os.getpid()))
    signal.signal(signal.SIGTERM, interrompre)
    signal.signal(signal.SIGINT, interrompre)
    debut = time.monotonic()
    noter("boucle_debut", vague=vague, modele=args.modele, pid=os.getpid())
    try:
        while True:
            prets, sautes = a_traiter(vague, args.ticket)
            if not prets:
                for t in sautes:
                    noter("saute", ticket=t["id"], motif="dépendance non faite", depend=t["depend"])
                break
            traiter(prets[0], args)
    except Exception as exc:  # la boucle s'arrête, l'erreur reste dans le journal
        noter("boucle_erreur", erreur=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        FICHIER_PID.unlink(missing_ok=True)
    _, tickets = lire_etat()
    etats = {
        i: t["etat"] for i, t in tickets.items() if i.startswith(f"V{vague}-")
    }
    noter("boucle_fin", vague=vague, duree_s=round(time.monotonic() - debut), etats=etats)
    return 0


def main() -> int:
    analyseur = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    analyseur.add_argument("vague", nargs="?", type=int, help="numéro de la vague")
    analyseur.add_argument("--ticket", help="ne traiter que ce ticket")
    analyseur.add_argument("--modele", default="qwen-local", help="nom du modèle à la passerelle")
    analyseur.add_argument("--delai-session", type=int, default=120, help="minutes par session")
    analyseur.add_argument("--nettoyer", action="store_true", help="met de côté un arbre de travail non propre")
    analyseur.add_argument("--garder", action="store_true", help="reprend avec l'arbre de travail tel qu'il est")
    analyseur.add_argument("--executant-factice", metavar="CMD", help="commande shell lancée à la place de claude (essais de la boucle)")
    analyseur.add_argument("--intervention", metavar="TEXTE", help="note une intervention humaine dans le journal et sort")
    analyseur.add_argument("--auteur", default="Thomas", help="auteur de l'intervention")
    analyseur.add_argument("--remettre", metavar="ID", help="remet un ticket bloqué à « à faire », compteur à zéro")
    args = analyseur.parse_args()

    if args.intervention:
        noter("intervention", auteur=args.auteur, texte=args.intervention)
        return 0
    if args.remettre:
        _, tickets = lire_etat()
        if args.remettre not in tickets:
            print(f"Ticket inconnu : {args.remettre}", file=sys.stderr)
            return 2
        ecrire_ticket(args.remettre, etat="à faire", tentatives=0, commit="")
        noter("remise", ticket=args.remettre)
        print(f"{args.remettre} remis à « à faire ». Fais le commit de tickets/ETAT.md avant de relancer.")
        return 0
    if args.vague is None:
        analyseur.error("numéro de vague manquant")
    return boucle(args)


if __name__ == "__main__":
    sys.exit(main())
