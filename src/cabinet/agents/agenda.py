"""Agent d'agenda : évènements, séries et réunions depuis le fichier ICS.

L'agent charge la fenêtre de 30 jours avant à 14 jours après aujourd'hui
(specs/05 §6.2), met à jour `event` par (`uid`, `start`) et crée la série et
la réunion de chaque évènement qui n'est ni annulé ni sur la journée entière.
Il ne supprime jamais rien : un évènement retiré du fichier reste en base, et
la réunion d'un évènement devenu annulé aussi (specs/05 §6.2, specs/05 §7).
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from cabinet import clock
from cabinet.agents.base import AgentOutcome
from cabinet.db.models import Event, Meeting, Series
from cabinet.ports.calendar import CalendarSource, EventData
from cabinet.services.series import series_key

_WINDOW_BEFORE_DAYS = 30
_WINDOW_AFTER_DAYS = 14


class AgendaAgent:
    """Charge l'agenda ICS et met à jour évènements, séries et réunions (specs/05 §6.2)."""

    name = "agenda"

    def __init__(self, calendar: CalendarSource) -> None:
        self._calendar = calendar

    def run(self, session: Session) -> AgentOutcome:
        """Un passage : chaque occurrence de la fenêtre met à jour sa ligne."""
        today = clock.today()
        events = self._calendar.list_events(
            today - timedelta(days=_WINDOW_BEFORE_DAYS),
            today + timedelta(days=_WINDOW_AFTER_DAYS),
        )
        created = 0
        for data in events:
            row, is_new = _upsert_event(session, data)
            if is_new:
                created += 1
            if not data.cancelled and not data.all_day:
                _upsert_meeting(session, row, data)
        session.commit()
        return AgentOutcome(processed=created)


def _upsert_event(session: Session, data: EventData) -> tuple[Event, bool]:
    """La ligne `event` de mêmes `uid` et `start`, créée ou mise à jour."""
    fields = data.model_dump()
    row = session.scalar(
        select(Event).where(Event.uid == fields["uid"], Event.start == fields["start"])
    )
    if row is not None:
        for name, value in fields.items():
            if name not in ("uid", "start"):
                setattr(row, name, value)
        return row, False
    row = Event(**fields)
    session.add(row)
    session.flush()  # l'identifiant est nécessaire à la réunion
    return row, True


def _upsert_meeting(session: Session, row: Event, data: EventData) -> None:
    """La série et la réunion d'un évènement, créées si elles manquent (specs/05 §6.2)."""
    series = _series_for(session, data)
    meeting = session.scalar(select(Meeting).where(Meeting.event_id == row.id))
    if meeting is None:
        session.add(
            Meeting(
                event_id=row.id,
                series_id=series.id,
                title=data.title,
                date=data.start.date(),
            )
        )
    else:
        meeting.title = data.title
        meeting.date = data.start.date()


def _series_for(session: Session, data: EventData) -> Series:
    """La série de la clé du titre, créée si elle manque (specs/05 §6.2)."""
    key = series_key(data.title)
    series = session.scalar(select(Series).where(Series.key == key))
    if series is None:
        series = Series(key=key, label=data.title, followed=False)
        session.add(series)
        session.flush()  # l'identifiant est nécessaire à la réunion
    return series
