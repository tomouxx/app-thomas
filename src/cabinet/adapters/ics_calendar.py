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

    def __init__(self, path: Path):
        self._path = path

    def list_events(self, start: date, end: date) -> list[EventData]:
        """Les occurrences de la fenêtre : le jour `end` est rendu, pas le lendemain."""
        calendrier = _lire_calendrier(self._path)
        debut_fenetre = datetime.combine(start, time.min, tzinfo=clock.LOCAL_TZ)
        fin_fenetre = datetime.combine(end + timedelta(days=1), time.min, tzinfo=clock.LOCAL_TZ)
        occurrences = recurring_ical_events.of(calendrier).between(debut_fenetre, fin_fenetre)
        evenements = [_evenement_data(occurrence) for occurrence in occurrences]
        return sorted(evenements, key=lambda evenement: (evenement.start, evenement.uid))


def _lire_calendrier(path: Path) -> icalendar.Calendar:
    """Le calendrier d'un fichier ICS, ou une `CalendarError` lisible (specs/05 §6.1)."""
    if not path.is_file():
        raise CalendarError(
            f"Fichier d'agenda introuvable : {path}. "
            "Exporte ton agenda au format ICS et place-le à cet endroit."
        )
    try:
        octets = path.read_bytes()
        return icalendar.Calendar.from_ical(octets)
    except Exception as error:
        raise CalendarError(f"Fichier d'agenda illisible : {error}.") from error


def _evenement_data(occurrence: icalendar.Event) -> EventData:
    """Un `EventData` pour une occurrence développée (tableau de specs/05 §6.1)."""
    all_day = not isinstance(_value(occurrence.get("DTSTART")), datetime)
    debut = _instant(occurrence.get("DTSTART"))
    brut_fin = occurrence.get("DTEND")
    fin = _instant(brut_fin) if brut_fin is not None else debut
    titre = str(occurrence.get("SUMMARY") or "(sans titre)")
    return EventData(
        uid=str(occurrence.get("UID") or ""),
        title=titre,
        start=debut,
        end=fin,
        all_day=all_day,
        location=_text(occurrence.get("LOCATION")),
        organizer=_adresse(occurrence.get("ORGANIZER")),
        attendees=_participants(occurrence),
        cancelled=_est_annule(occurrence, titre),
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


def _adresse(value: object) -> str | None:
    """Une adresse ICS sans `mailto:` et en minuscules, ou `None` (specs/05 §6.1)."""
    if value is None:
        return None
    text = str(value)
    if text.lower().startswith("mailto:"):
        text = text[len("mailto:") :]
    return text.lower() or None


def _participants(occurrence: icalendar.Event) -> list[str]:
    """Les adresses `ATTENDEE`, dans l'ordre du fichier, sans doublon."""
    vus: set[str] = set()
    participants: list[str] = []
    for value in occurrence.get("ATTENDEE") or []:
        adresse = _adresse(value)
        if adresse is not None and adresse not in vus:
            vus.add(adresse)
            participants.append(adresse)
    return participants


def _est_annule(occurrence: icalendar.Event, titre: str) -> bool:
    """Vrai si le statut est `CANCELLED` ou si le titre annonce l'annulation."""
    if str(occurrence.get("STATUS") or "").upper() == "CANCELLED":
        return True
    return titre.casefold().startswith(("annulé", "canceled"))
