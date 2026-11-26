"""Modèles de données (specs/02 §2).

Les quatorze tables de la version 1, dans l'ordre conseillé par
`specs/02 §2`. Les états sont des énumérations de chaînes stockées sous
leur valeur (specs/02 §1.5) ; les dates et heures sont conscientes du
fuseau via `TZDateTime` (specs/02 §1.1). Les listes et objets libres sont
des colonnes `JSON` (specs/02 §1.7).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cabinet.db.base import Base, TZDateTime


def _enum_type(members: type[StrEnum]) -> Enum:
    """Type de colonne d'un état, stocké sous sa valeur (specs/02 §1.5)."""
    return Enum(members, native_enum=False, values_callable=lambda m: [x.value for x in m])


class RunStatus(StrEnum):
    """État d'une exécution d'agent (specs/02 §2.1)."""

    EN_COURS = "en_cours"
    OK = "ok"
    ERREUR = "erreur"


class InboxKind(StrEnum):
    """Nature d'un fichier déposé dans `entree/` (specs/02 §2.2)."""

    TEAMS_TRANSCRIPT = "teams_transcript"
    NOTES = "notes"
    AUDIO = "audio"
    APP_CAPTURE_TEXT = "app_capture_text"
    APP_CAPTURE_IMAGE = "app_capture_image"
    INCONNU = "inconnu"


class InboxStatus(StrEnum):
    """État d'un élément d'entrée (specs/02 §2.2.1)."""

    NOUVEAU = "nouveau"
    A_RATTACHER = "a_rattacher"
    CONSENTEMENT_REQUIS = "consentement_requis"
    PRET = "pret"
    TRAITE = "traite"
    ERREUR = "erreur"
    IGNORE = "ignore"


class TranscriptSource(StrEnum):
    """Origine du texte d'une réunion (specs/02 §2.6)."""

    TEAMS = "teams"
    AUDIO = "audio"
    NOTES = "notes"


class DocStatus(StrEnum):
    """État d'un document généré : fiche ou compte rendu (specs/02 §2.7, §2.8)."""

    BROUILLON = "brouillon"
    VALIDE = "valide"


class ActionStatus(StrEnum):
    """État d'une action (specs/02 §2.10)."""

    OUVERT = "ouvert"
    FAIT = "fait"


class CaptureSource(StrEnum):
    """Moyen de lecture d'une capture d'imputations (specs/02 §2.11)."""

    TEXTE = "texte"
    VISION = "vision"


class Severity(StrEnum):
    """Gravité d'une anomalie (specs/02 §2.13)."""

    BLOQUANT = "bloquant"
    A_VERIFIER = "a_verifier"


class DraftKind(StrEnum):
    """Nature d'un brouillon de message (specs/02 §2.14)."""

    ENVOI_CR = "envoi_cr"
    RELANCE_CRA = "relance_cra"


class DraftStatus(StrEnum):
    """État d'un brouillon de message (specs/02 §2.14)."""

    BROUILLON = "brouillon"
    VALIDE = "valide"
    REJETE = "rejete"
    ENVOYE_A_LA_MAIN = "envoye_a_la_main"


class AgentRun(Base):
    """Une ligne du journal des exécutions d'agent (specs/02 §2.1).

    Le journal ne contient jamais le texte d'un prompt ni celui d'une
    réponse du modèle.
    """

    __tablename__ = "agent_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent: Mapped[str] = mapped_column(String(50), nullable=False)
    target_ref: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    status: Mapped[RunStatus] = mapped_column(
        Enum(
            RunStatus,
            native_enum=False,
            values_callable=lambda members: [member.value for member in members],
        ),
        nullable=False,
        default=RunStatus.EN_COURS,
    )
    started_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class InboxItem(Base):
    """Un fichier déposé dans `entree/` (specs/02 §2.2)."""

    __tablename__ = "inbox_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    kind: Mapped[InboxKind] = mapped_column(_enum_type(InboxKind), nullable=False)
    status: Mapped[InboxStatus] = mapped_column(
        _enum_type(InboxStatus), nullable=False, default=InboxStatus.NOUVEAU
    )
    name_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    meeting_id: Mapped[int | None] = mapped_column(
        ForeignKey("meeting.id", ondelete="SET NULL"), nullable=True
    )
    consent_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    consent_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)

    meeting: Mapped[Meeting | None] = relationship()


class Event(Base):
    """Une occurrence d'évènement de l'agenda (specs/02 §2.3)."""

    __tablename__ = "event"
    __table_args__ = (UniqueConstraint("uid", "start"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uid: Mapped[str] = mapped_column(String(300), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    start: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    end: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    all_day: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    location: Mapped[str | None] = mapped_column(String(300), nullable=True)
    organizer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    attendees: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    cancelled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Series(Base):
    """Une série de réunions de même titre (specs/02 §2.4)."""

    __tablename__ = "series"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(300), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(300), nullable=False)
    followed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    client: Mapped[str | None] = mapped_column(String(200), nullable=True)


class Meeting(Base):
    """Une réunion, avec ou sans évènement (specs/02 §2.5)."""

    __tablename__ = "meeting"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("event.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)

    event: Mapped[Event | None] = relationship()
    series: Mapped[Series] = relationship()
    transcripts: Mapped[list[Transcript]] = relationship(
        cascade="all, delete-orphan", back_populates="meeting"
    )
    prep_note: Mapped[PrepNote | None] = relationship(
        uselist=False, cascade="all, delete-orphan", back_populates="meeting"
    )
    minutes: Mapped[list[Minutes]] = relationship(
        cascade="all, delete-orphan", back_populates="meeting"
    )


class Transcript(Base):
    """Le texte d'une source rattachée à une réunion (specs/02 §2.6)."""

    __tablename__ = "transcript"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[int] = mapped_column(
        ForeignKey("meeting.id", ondelete="CASCADE"), nullable=False
    )
    inbox_item_id: Mapped[int] = mapped_column(ForeignKey("inbox_item.id"), nullable=False)
    source: Mapped[TranscriptSource] = mapped_column(_enum_type(TranscriptSource), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    segments: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)

    meeting: Mapped[Meeting] = relationship(back_populates="transcripts")
    inbox_item: Mapped[InboxItem] = relationship()


class PrepNote(Base):
    """La fiche de préparation d'une réunion (specs/02 §2.7)."""

    __tablename__ = "prep_note"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[int] = mapped_column(
        ForeignKey("meeting.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[DocStatus] = mapped_column(
        _enum_type(DocStatus), nullable=False, default=DocStatus.BROUILLON
    )
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)

    meeting: Mapped[Meeting] = relationship(back_populates="prep_note")


class Minutes(Base):
    """Une version du compte rendu d'une réunion (specs/02 §2.8)."""

    __tablename__ = "minutes"
    __table_args__ = (UniqueConstraint("meeting_id", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[int] = mapped_column(
        ForeignKey("meeting.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    open_points: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    next_meeting: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[DocStatus] = mapped_column(
        _enum_type(DocStatus), nullable=False, default=DocStatus.BROUILLON
    )
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)

    meeting: Mapped[Meeting] = relationship(back_populates="minutes")
    decisions: Mapped[list[Decision]] = relationship(
        cascade="all, delete-orphan", back_populates="minutes"
    )
    action_items: Mapped[list[ActionItem]] = relationship(
        cascade="all, delete-orphan", back_populates="minutes"
    )


class Decision(Base):
    """Une décision d'un compte rendu (specs/02 §2.9)."""

    __tablename__ = "decision"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[int] = mapped_column(
        ForeignKey("meeting.id", ondelete="CASCADE"), nullable=False
    )
    minutes_id: Mapped[int] = mapped_column(
        ForeignKey("minutes.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    meeting: Mapped[Meeting] = relationship()
    minutes: Mapped[Minutes] = relationship(back_populates="decisions")


class ActionItem(Base):
    """Une action d'un compte rendu (specs/02 §2.10)."""

    __tablename__ = "action_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[int] = mapped_column(
        ForeignKey("meeting.id", ondelete="CASCADE"), nullable=False
    )
    minutes_id: Mapped[int] = mapped_column(
        ForeignKey("minutes.id", ondelete="CASCADE"), nullable=False
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[ActionStatus] = mapped_column(
        _enum_type(ActionStatus), nullable=False, default=ActionStatus.OUVERT
    )

    meeting: Mapped[Meeting] = relationship()
    minutes: Mapped[Minutes] = relationship(back_populates="action_items")


class TimesheetCapture(Base):
    """Une capture d'imputations (specs/02 §2.11)."""

    __tablename__ = "timesheet_capture"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inbox_item_id: Mapped[int] = mapped_column(ForeignKey("inbox_item.id"), nullable=False)
    period: Mapped[str | None] = mapped_column(String(7), nullable=True)
    source: Mapped[CaptureSource] = mapped_column(_enum_type(CaptureSource), nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verified_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    checked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    inbox_item: Mapped[InboxItem] = relationship()
    lines: Mapped[list[TimesheetLine]] = relationship(
        cascade="all, delete-orphan", back_populates="capture"
    )
    anomalies: Mapped[list[Anomaly]] = relationship(
        cascade="all, delete-orphan", back_populates="capture"
    )


class TimesheetLine(Base):
    """Une ligne d'une capture (specs/02 §2.12)."""

    __tablename__ = "timesheet_line"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    capture_id: Mapped[int] = mapped_column(
        ForeignKey("timesheet_capture.id", ondelete="CASCADE"), nullable=False
    )
    person: Mapped[str] = mapped_column(String(200), nullable=False)
    project_code: Mapped[str] = mapped_column(String(100), nullable=False)
    days: Mapped[float] = mapped_column(Float, nullable=False)

    capture: Mapped[TimesheetCapture] = relationship(back_populates="lines")


class Anomaly(Base):
    """Une anomalie relevée sur une capture relue (specs/02 §2.13)."""

    __tablename__ = "anomaly"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    capture_id: Mapped[int] = mapped_column(
        ForeignKey("timesheet_capture.id", ondelete="CASCADE"), nullable=False
    )
    person: Mapped[str] = mapped_column(String(200), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(10), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[Severity] = mapped_column(_enum_type(Severity), nullable=False)

    capture: Mapped[TimesheetCapture] = relationship(back_populates="anomalies")


class Draft(Base):
    """Un brouillon de message (specs/02 §2.14). L'application n'envoie rien."""

    __tablename__ = "draft"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[DraftKind] = mapped_column(_enum_type(DraftKind), nullable=False)
    recipients: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[DraftStatus] = mapped_column(
        _enum_type(DraftStatus), nullable=False, default=DraftStatus.BROUILLON
    )
    meeting_id: Mapped[int | None] = mapped_column(
        ForeignKey("meeting.id", ondelete="SET NULL"), nullable=True
    )
    capture_id: Mapped[int | None] = mapped_column(
        ForeignKey("timesheet_capture.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)

    meeting: Mapped[Meeting | None] = relationship()
    capture: Mapped[TimesheetCapture | None] = relationship()
