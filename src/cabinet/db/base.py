"""Base, moteur, sessions et type de date consciente du fuseau (specs/05 §2.1).

Le chemin de la base est résolu à chaque appel : changer `CABINET_HOME`
entre deux appels donne un autre moteur et une autre base. Le cache par
chemin est le seul état global toléré hors de la configuration.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

from sqlalchemy import DateTime, Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session
from sqlalchemy.types import TypeDecorator

from cabinet import clock


class Base(DeclarativeBase):
    """Base commune des modèles (specs/02 §1)."""


class TZDateTime(TypeDecorator):
    """Date et heure conscientes du fuseau (specs/02 §1.1).

    Écriture : conversion en UTC, fuseau retiré (SQLite ne le garde pas).
    Lecture : la valeur reçoit le fuseau UTC puis est convertie dans LOCAL_TZ.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Une date sans fuseau ne peut pas être écrite dans la base.")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC).astimezone(clock.LOCAL_TZ)


def _chemin_base() -> Path:
    """Fichier de la base, calculé sur place (avant V1-01, specs/03 §1)."""
    maison = os.environ.get("CABINET_HOME", "").strip()
    home = Path(maison).expanduser().resolve() if maison else Path("~/Cabinet").expanduser()
    return home / "data" / "cabinet.db"


@cache
def _moteur(chemin: str) -> Engine:
    """Moteur de SQLite, mis en cache sur le chemin (specs/05 §2.1)."""
    moteur = create_engine(f"sqlite:///{chemin}", connect_args={"timeout": 30})

    @event.listens_for(moteur, "connect")
    def _activer_cles_etrangeres(connexion: Any, _identification: Any) -> None:
        # Connexion brute de DBAPI (sqlite3), pas une connexion SQLAlchemy.
        connexion.execute("PRAGMA foreign_keys=ON")

    return moteur


def get_engine() -> Engine:
    """Moteur de la base du répertoire courant, chemin résolu à chaque appel."""
    chemin = _chemin_base()
    chemin.parent.mkdir(parents=True, exist_ok=True)
    return _moteur(str(chemin))


def get_session() -> Session:
    """Session neuve à chaque appel ; l'appelant la ferme (specs/05 §2.1)."""
    return Session(get_engine(), expire_on_commit=False)


def init_db() -> None:
    """Crée les tables si elles manquent ; un second appel ne perd rien."""
    import cabinet.db.models  # noqa: F401 - les modèles s'enregistrent sur Base

    Base.metadata.create_all(get_engine())
