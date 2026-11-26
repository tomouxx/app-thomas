"""Port de l'agenda (specs/01 §3.4, specs/02 §3.2).

Le port décrit ce que l'agent d'agenda attend d'une source d'évènements :
les occurrences d'une fenêtre de jours, sans rien savoir du format ni de
l'emplacement. L'adaptateur de la version 1 est `IcsCalendarSource`
(`src/cabinet/adapters/ics_calendar.py`).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol

from pydantic import BaseModel


class CalendarError(Exception):
    """Erreur de lecture de l'agenda, avec un message lisible (specs/05 §6.1)."""


class EventData(BaseModel):
    """Une occurrence d'évènement de l'agenda (specs/02 §2.3).

    Les instants sont dans le fuseau local (`clock.LOCAL_TZ`) ; les adresses
    sont sans `mailto:` et en minuscules.
    """

    uid: str
    title: str
    start: datetime
    end: datetime
    all_day: bool  # vrai si l'évènement couvre une journée entière
    location: str | None
    organizer: str | None
    attendees: list[str]  # dans l'ordre du fichier, sans doublon
    cancelled: bool


class CalendarSource(Protocol):
    """Port de l'agenda (specs/01 §3.4)."""

    def list_events(self, start: date, end: date) -> list[EventData]:
        """Les occurrences de la fenêtre, du jour `start` au jour `end` inclus."""
        ...
