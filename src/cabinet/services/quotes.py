"""Contrôle des extraits cités dans un compte rendu (specs/06 §5).

Le contrôle est déterministe : il n'appelle jamais le modèle et ne corrige
jamais un extrait. Un extrait est trouvé s'il figure dans la source après
normalisation (score 1.0), ou si une fenêtre glissante de la source atteint
un score de similarité au moins égal au seuil.
"""

from __future__ import annotations

import difflib
import unicodedata

from pydantic import BaseModel


class QuoteCheck(BaseModel):
    """Résultat du contrôle d'un extrait contre la source (specs/02 §3.5)."""

    found: bool
    score: float


_MIN_QUOTE_CHARS = 10


def normalize(text: str) -> str:
    """Minuscules, sans accents, ponctuation remplacée par des espaces (specs/06 §5).

    Tout caractère non alphanumérique devient une espace ; les suites
    d'espaces sont réduites à une et celles des bords retirées.
    """
    decomposed = unicodedata.normalize("NFKD", text.lower())
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    spaced = "".join(" " if not ch.isalnum() else ch for ch in without_accents)
    return " ".join(spaced.split())


def verify_quote(quote: str, source: str, threshold: float = 0.85) -> QuoteCheck:
    """Contrôle un extrait contre la source (specs/06 §5).

    `found` est vrai si l'extrait normalisé figure dans la source (score 1.0)
    ou si le meilleur score de fenêtre glissante atteint `threshold`. Un
    extrait normalisé de moins de 10 caractères n'est jamais trouvé, même s'il
    existe dans la source.
    """
    normalized_quote = normalize(quote)
    if len(normalized_quote) < _MIN_QUOTE_CHARS:
        return QuoteCheck(found=False, score=0.0)
    normalized_source = normalize(source)
    if normalized_quote in normalized_source:
        return QuoteCheck(found=True, score=1.0)
    best = _best_window_score(normalized_quote, normalized_source)
    return QuoteCheck(found=best >= threshold, score=best)


def _best_window_score(quote: str, source: str) -> float:
    """Meilleur score de l'extrait sur les fenêtres glissantes de la source.

    Fenêtres de la longueur de l'extrait, par pas d'un quart de cette
    longueur (au moins 1) ; si la source est plus courte, elle est comparée
    en entier. Un seul `SequenceMatcher` sert à toutes les fenêtres, et
    `ratio()` n'est calculé que si `quick_ratio()` dépasse le meilleur score.
    """
    matcher = difflib.SequenceMatcher(None, "", quote, autojunk=False)
    if len(source) <= len(quote):
        return _window_score(matcher, source, 0.0)
    best = 0.0
    step = max(len(quote) // 4, 1)
    for start in range(0, len(source) - len(quote) + 1, step):
        window = source[start : start + len(quote)]
        best = max(best, _window_score(matcher, window, best))
    return best


def _window_score(matcher: difflib.SequenceMatcher, window: str, best: float) -> float:
    """Le score d'une fenêtre, ou 0.0 si `quick_ratio()` ne le dépasse pas."""
    matcher.set_seq1(window)
    if matcher.quick_ratio() <= best:
        return 0.0
    return matcher.ratio()
