"""Agenda par fichier ICS : lecture des occurrences d'une fenêtre (specs/05 §6.1).

`IcsCalendarSource` est l'adaptateur du port `CalendarSource` pour la version
1 : il lit un fichier ICS local, développe les récurrences sur la fenêtre
demandée et convertit toutes les heures dans le fuseau local. Il ne lit jamais
une adresse web et n'écrit jamais dans l'agenda (specs/05 §6.1).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path

import icalendar
import recurring_ical_events

from cabinet import clock
from cabinet.ports.calendar import CalendarError, EventData


class IcsCalendarSource:
    """Lit les occurrences d'un fichier ICS local (specs/05 §6.1)."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def list_events(self, start: date, end: date) -> list[EventData]:
        """Les occurrences de la fenêtre : le jour `end` est rendu, pas le lendemain."""
        calendar = _read_calendar(self._path)
        window_start = datetime.combine(start, time.min, tzinfo=clock.LOCAL_TZ)
        window_end = datetime.combine(end + timedelta(days=1), time.min, tzinfo=clock.LOCAL_TZ)
        occurrences = recurring_ical_events.of(calendar).between(window_start, window_end)
        events = [_event_data(occurrence) for occurrence in occurrences]
        return sorted(events, key=lambda event: (event.start, event.uid))


def _read_calendar(path: Path) -> icalendar.Calendar:
    """Le calendrier d'un fichier ICS, ou une `CalendarError` lisible (specs/05 §6.1)."""
    if not path.is_file():
        raise CalendarError(
            f"Fichier d'agenda introuvable : {path}. "
            "Exporte ton agenda au format ICS et place-le à cet endroit."
        )
    try:
        data = path.read_bytes()
        return icalendar.Calendar.from_ical(data)
    except Exception as error:
        raise CalendarError(f"Fichier d'agenda illisible : {error}.") from error


def _event_data(occurrence: icalendar.Event) -> EventData:
    """Un `EventData` pour une occurrence développée (tableau de specs/05 §6.1)."""
    all_day = not isinstance(_value(occurrence.get("DTSTART")), datetime)
    start = _instant(occurrence.get("DTSTART"))
    raw_end = occurrence.get("DTEND")
    end = _instant(raw_end) if raw_end is not None else start
    title = str(occurrence.get("SUMMARY") or "(sans titre)")
    return EventData(
        uid=str(occurrence.get("UID") or ""),
        title=title,
        start=start,
        end=end,
        all_day=all_day,
        location=_text(occurrence.get("LOCATION")),
        organizer=_address(occurrence.get("ORGANIZER")),
        attendees=_participants(occurrence),
        cancelled=_is_cancelled(occurrence, title),
    )


def _value(prop: object) -> object:
    """La valeur d'une propriété ICS, sans son enveloppe `vDDDTypes`."""
    return getattr(prop, "dt", prop)


def _instant(value: object) -> datetime:
    """Une valeur ICS en `datetime` du fuseau local (specs/05 §6.1).

    Une heure sans fuseau est prise dans `LOCAL_TZ` ; une date sans heure
    donne minuit de ce jour dans `LOCAL_TZ`.
    """
    value = _value(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=clock.LOCAL_TZ)
        return value.astimezone(clock.LOCAL_TZ)
    return datetime.combine(value, time.min, tzinfo=clock.LOCAL_TZ)


def _text(value: object) -> str | None:
    """Un texte ICS, ou `None` s'il est absent ou vide."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _address(value: object) -> str | None:
    """Une adresse ICS sans `mailto:` et en minuscules, ou `None` (specs/05 §6.1)."""
    if value is None:
        return None
    text = str(value)
    if text.lower().startswith("mailto:"):
        text = text[len("mailto:") :]
    return text.lower() or None


def _participants(occurrence: icalendar.Event) -> list[str]:
    """Les adresses `ATTENDEE`, dans l'ordre du fichier, sans doublon."""
    seen: set[str] = set()
    participants: list[str] = []
    for value in occurrence.get("ATTENDEE") or []:
        address = _address(value)
        if address is not None and address not in seen:
            seen.add(address)
            participants.append(address)
    return participants


def _is_cancelled(occurrence: icalendar.Event, title: str) -> bool:
    """Vrai si le statut est `CANCELLED` ou si le titre annonce l'annulation."""
    if str(occurrence.get("STATUS") or "").upper() == "CANCELLED":
        return True
    return title.casefold().startswith(("annulé", "canceled"))
