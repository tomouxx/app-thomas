"""Répertoire de données, chemins et réglages (specs/03 §1 à §3).

`paths()` et `load_settings()` ne gardent rien en mémoire : chaque appel
relit la variable d'environnement ou le fichier (specs/03 §7).
"""

from __future__ import annotations

import os
import re
import tempfile
import tomllib
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from cabinet.llm.client import assert_loopback
from cabinet.llm.types import ConfigError

DEFAULT_SETTINGS_TOML = """[llm]
base_url = "http://127.0.0.1:8080/v1"
model = "qwen-local"
timeout_s = 900
context_tokens = 64000

[transcription]
model = "mlx-community/whisper-large-v3-mlx"
delete_audio_after_validation = true

[agenda]
ics_path = "config/agenda.ics"

[cra]
# deadline = 2026-11-30

[cra.regles]
R1 = true
R2 = true
R3 = true
R4 = true
tolerance_jours = 0.0

[ui]
port = 8765
"""


class Paths(BaseModel):
    """Chemins du répertoire de données (specs/03 §1)."""

    home: Path
    inbox: Path  # home / "entree"
    processed: Path  # home / "traite"
    drafts: Path  # home / "brouillons"
    config: Path  # home / "config"
    data: Path  # home / "data"

    @property
    def settings_file(self) -> Path:
        return self.config / "reglages.toml"

    @property
    def roster_file(self) -> Path:
        return self.config / "equipe.yaml"

    @property
    def db_file(self) -> Path:
        return self.data / "cabinet.db"

    @property
    def lock_file(self) -> Path:
        return self.data / "pipeline.lock"


def paths() -> Paths:
    """Chemins du répertoire de données, relus à chaque appel (specs/03 §1)."""
    home = _home()
    return Paths(
        home=home,
        inbox=home / "entree",
        processed=home / "traite",
        drafts=home / "brouillons",
        config=home / "config",
        data=home / "data",
    )


def _home() -> Path:
    cabinet_home = os.environ.get("CABINET_HOME")
    if cabinet_home:
        return Path(cabinet_home).expanduser().resolve()
    return Path("~/Cabinet").expanduser()


class StrictModel(BaseModel):
    """Modèle qui refuse les clés inconnues (specs/03 §3)."""

    model_config = ConfigDict(extra="forbid")


class LLMSettings(StrictModel):
    """Section [llm] de reglages.toml."""

    base_url: str = "http://127.0.0.1:8080/v1"
    model: str = "qwen-local"  # alias, voir specs/00 §4.4
    timeout_s: float = 900  # délai d'un appel, voir specs/00 §4.2
    context_tokens: int = 64000


class TranscriptionSettings(StrictModel):
    """Section [transcription] de reglages.toml."""

    model: str = "mlx-community/whisper-large-v3-mlx"
    delete_audio_after_validation: bool = True


class AgendaSettings(StrictModel):
    """Section [agenda] de reglages.toml."""

    ics_path: Path = Path("config/agenda.ics")  # relatif au répertoire de données, ou absolu


class RulesSettings(StrictModel):
    """Section [cra.regles] de reglages.toml."""

    R1: bool = True
    R2: bool = True
    R3: bool = True
    R4: bool = True
    tolerance_jours: float = 0.0  # jours manquants tolérés par R1


class CraSettings(StrictModel):
    """Section [cra] de reglages.toml."""

    deadline: date | None = None  # échéance des imputations du mois
    regles: RulesSettings = RulesSettings()


class UISettings(StrictModel):
    """Section [ui] de reglages.toml."""

    port: int = 8765


class Settings(StrictModel):
    """Réglages complets de reglages.toml (specs/03 §3)."""

    llm: LLMSettings = LLMSettings()
    transcription: TranscriptionSettings = TranscriptionSettings()
    agenda: AgendaSettings = AgendaSettings()
    cra: CraSettings = CraSettings()
    ui: UISettings = UISettings()


def load_settings() -> Settings:
    """Réglages de reglages.toml, relus à chaque appel (specs/03 §3.1)."""
    settings = _validate(_read_settings_file())
    assert_loopback(settings.llm.base_url)
    _resolve_agenda_path(settings)
    return settings


def _read_settings_file() -> dict[str, Any]:
    path = paths().settings_file
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as stream:
            return tomllib.load(stream)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"reglages.toml est illisible : {error}.") from error


def _validate(raw: dict[str, Any]) -> Settings:
    try:
        return Settings.model_validate(raw)
    except ValidationError as error:
        raise _settings_error(error) from error


def _settings_error(error: ValidationError) -> ConfigError:
    flaw = error.errors()[0]
    key = ".".join(str(part) for part in flaw["loc"])
    if flaw["type"] == "extra_forbidden":
        return ConfigError(f"Clé inconnue dans reglages.toml : {key}.")
    return ConfigError(f"Valeur invalide pour {key} : {flaw['msg']}.")


def _resolve_agenda_path(settings: Settings) -> None:
    """Un chemin d'agenda relatif est résolu dans le répertoire de données."""
    if not settings.agenda.ics_path.is_absolute():
        settings.agenda.ics_path = paths().home / settings.agenda.ics_path


_CRA_HEADER_RE = re.compile(r"^\s*\[cra\]\s*(#.*)?$")
_DEADLINE_RE = re.compile(r"^\s*deadline\s*=")


def set_cra_deadline(day: date | None) -> None:
    """Seule écriture de l'application dans reglages.toml (specs/03 §3.2)."""
    path = paths().settings_file
    lines = _settings_lines(path)
    updated, changed = _apply_deadline(lines, day)
    if changed:
        _write_atomic(path, "".join(updated))


def _settings_lines(path: Path) -> list[str]:
    """Lignes du fichier avec leurs fins de ligne, ou le texte par défaut."""
    if not path.exists():
        return DEFAULT_SETTINGS_TOML.splitlines(keepends=True)
    return path.read_text(encoding="utf-8").splitlines(keepends=True)


def _cra_section(lines: list[str]) -> tuple[int, int] | None:
    """Bornes [début, fin) de la section [cra], ou None si elle est absente."""
    for start, line in enumerate(lines):
        if _CRA_HEADER_RE.match(line):
            end = len(lines)
            for index in range(start + 1, len(lines)):
                if lines[index].lstrip().startswith("["):
                    end = index
                    break
            return start, end
    return None


def _line_ending(line: str) -> str:
    for ending in ("\r\n", "\n"):
        if line.endswith(ending):
            return ending
    return ""


def _apply_deadline(lines: list[str], day: date | None) -> tuple[list[str], bool]:
    section = _cra_section(lines)
    if day is None:
        return _remove_deadline(lines, section)
    result = list(lines)
    if section is None:
        return _append_cra_section(result, day), True
    start, end = section
    for index in range(start + 1, end):
        if _DEADLINE_RE.match(result[index]):
            result[index] = f"deadline = {day.isoformat()}{_line_ending(result[index])}"
            return result, True
    inserted = f"deadline = {day.isoformat()}{_line_ending(result[start])}"
    result.insert(start + 1, inserted)
    return result, True


def _remove_deadline(lines: list[str], section: tuple[int, int] | None) -> tuple[list[str], bool]:
    if section is None:
        return lines, False
    start, end = section
    for index in range(start + 1, end):
        if _DEADLINE_RE.match(lines[index]):
            result = list(lines)
            del result[index]
            return result, True
    return lines, False


def _append_cra_section(lines: list[str], day: date) -> list[str]:
    result = list(lines)
    if result and not _line_ending(result[-1]):
        result[-1] += "\n"
    result.extend(("\n", "[cra]\n", f"deadline = {day.isoformat()}\n"))
    return result


def _write_atomic(path: Path, content: str) -> None:
    """Fichier temporaire du même dossier, puis renommage sur reglages.toml."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content.encode("utf-8"))
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
