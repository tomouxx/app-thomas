"""Lecteur des notes tapées par l'utilisateur (specs/06 §1.3).

Le lecteur transforme un fichier de notes en `TranscriptData` : le texte est
décodé en UTF-8 (avec repli cp1252), un éventuel bloc d'en-tête entre deux
lignes `---` en tête de fichier est retiré, et le reste forme un seul segment
sans orateur ni horodatage. Un fichier vide lève `ValueError` avec un message
en français (specs/06 §1). Le lecteur ne touche ni à la base ni au fichier.
"""

from __future__ import annotations

from pathlib import Path

from cabinet.ports.transcriber import Segment, TranscriptData


def read_notes(path: Path) -> TranscriptData:
    """Lit un fichier de notes et renvoie son texte en un seul segment."""
    raw = path.read_bytes()
    body = _strip_front_matter(_normalize_line_endings(_decode(raw))).strip()
    if not body:
        raise ValueError("Le fichier de notes est vide.")
    segment = Segment(start_s=None, end_s=None, speaker=None, text=body)
    return TranscriptData(text=segment.text, segments=[segment])


def _decode(raw: bytes) -> str:
    """UTF-8 (BOM accepté), repli cp1252 si l'octet n'est pas du UTF-8."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252")


def _normalize_line_endings(text: str) -> str:
    """Les fins de ligne Windows (`\r\n` et `\r`) deviennent des `\n`."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _strip_front_matter(text: str) -> str:
    """Retire un en-tête ouvert par `---` sur la première ligne.

    L'en-tête s'arrête à la prochaine ligne `---` (les deux incluses). Sans
    ligne de fermeture, tout le texte est conservé. Seule une ligne `---`
    exacte ouvre l'en-tête (interprétation notée dans le ticket).
    """
    lines = text.split("\n")
    if not lines or lines[0] != "---":
        return text
    for index in range(1, len(lines)):
        if lines[index] == "---":
            return "\n".join(lines[index + 1 :])
    return text
