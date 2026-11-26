"""Clé d'une série de réunions (specs/05 §6.3).

La clé regroupe sous une même série les occurrences d'une réunion, y compris
renommées (`RE:`, `TR:`, …), numérotées ou accentuées : elle ne contient ni
chiffre, ni ponctuation, ni accent. Deux réunions numérotées d'une même série
ont la même clé, c'est voulu (specs/05 §7).
"""

from __future__ import annotations

import re
import unicodedata

_PREFIXES_RE = re.compile(r"^(?:re|tr|fw|annule|canceled):\s*")
_NON_LETTER_RE = re.compile(r"[^a-z\s]")


def series_key(title: str) -> str:
    """La clé de série d'un titre (étapes 1 à 6 de specs/05 §6.3)."""
    cle = title.casefold()
    cle = unicodedata.normalize("NFKD", cle)
    cle = "".join(caractere for caractere in cle if not unicodedata.combining(caractere))
    while True:
        sans_prefixe = _PREFIXES_RE.sub("", cle, count=1)
        if sans_prefixe == cle:
            break
        cle = sans_prefixe
    cle = _NON_LETTER_RE.sub(" ", cle)
    cle = " ".join(cle.split())
    return cle or "sans titre"
