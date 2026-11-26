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

_FENETRE_AVANT_JOURS = 30
_FENETRE_APRES_JOURS = 14


class AgendaAgent:
    """Charge l'agenda ICS et met à jour évènements, séries et réunions (specs/05 §6.2)."""

    name = "agenda"

    def __init__(self, calendar: CalendarSource):
        self._calendar = calendar

    def run(self, session: Session) -> AgentOutcome:
        """Un passage : chaque occurrence de la fenêtre met à jour sa ligne."""
        aujourdhui = clock.today()
        evenements = self._calendar.list_events(
            aujourdhui - timedelta(days=_FENETRE_AVANT_JOURS),
            aujourdhui + timedelta(days=_FENETRE_APRES_JOURS),
        )
        crees = 0
        for data in evenements:
            ligne, cree = _upsert_evenement(session, data)
            if cree:
                crees += 1
            if not data.cancelled and not data.all_day:
                _upsert_reunion(session, ligne, data)
        session.commit()
        return AgentOutcome(processed=crees)


def _upsert_evenement(session: Session, data: EventData) -> tuple[Event, bool]:
    """La ligne `event` de mêmes `uid` et `start`, créée ou mise à jour."""
    champs = data.model_dump()
    ligne = session.scalar(
        select(Event).where(Event.uid == champs["uid"], Event.start == champs["start"])
    )
    if ligne is not None:
        for nom, value in champs.items():
            if nom not in ("uid", "start"):
                setattr(ligne, nom, value)
        return ligne, False
    ligne = Event(**champs)
    session.add(ligne)
    session.flush()  # l'identifiant est nécessaire à la réunion
    return ligne, True


def _upsert_reunion(session: Session, ligne: Event, data: EventData) -> None:
    """La série et la réunion d'un évènement, créées si elles manquent (specs/05 §6.2)."""
    serie = _serie_de(session, data)
    reunion = session.scalar(select(Meeting).where(Meeting.event_id == ligne.id))
    if reunion is None:
        session.add(
            Meeting(
                event_id=ligne.id,
                series_id=serie.id,
                title=data.title,
                date=data.start.date(),
            )
        )
    else:
        reunion.title = data.title
        reunion.date = data.start.date()


def _serie_de(session: Session, data: EventData) -> Series:
    """La série de la clé du titre, créée si elle manque (specs/05 §6.2)."""
    cle = series_key(data.title)
    serie = session.scalar(select(Series).where(Series.key == cle))
    if serie is None:
        serie = Series(key=cle, label=data.title, followed=False)
        session.add(serie)
        session.flush()  # l'identifiant est nécessaire à la réunion
    return serie
