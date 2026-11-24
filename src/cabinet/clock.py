"""Heure et jour courants, avec le fuseau local (specs/01 §3.6).

Ce module est le seul qui appelle `datetime.now`. Tout autre module importe
le module et écrit `clock.now()` ou `clock.today()`, pour qu'un test puisse
fixer la date en remplaçant `cabinet.clock.now`.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Europe/Zurich")


def now() -> datetime:
    """Heure courante, avec le fuseau LOCAL_TZ."""
    return datetime.now(LOCAL_TZ)


def today() -> date:
    """Jour courant dans LOCAL_TZ."""
    return now().date()
