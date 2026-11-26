"""Tests unitaires des lecteurs de transcripts Teams (V2-01).

Complètent les tests de contrat (`tests/contrats/test_v2_01_transcripts_teams.py`)
sur les cas qu'ils ne couvrent pas : les blocs STYLE et REGION d'un WebVTT,
les modèles du port, et un document `.docx` sans aucun texte. Chaque test
travaille dans un répertoire de données temporaire (`CABINET_HOME`, fixé par
`conftest.py`).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from docx import Document

from cabinet.ports.transcriber import Segment, TranscriptData
from cabinet.readers.teams_docx import read_docx
from cabinet.readers.teams_vtt import read_vtt

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "transcripts"


def test_vtt_avec_orateurs_donne_segments_et_texte() -> None:
    """Une réplique par cue, orateur du `<v>`, texte sans balises (specs/06 §1.1)."""
    data = read_vtt(FIXTURES / "point-hebdo.vtt")
    assert [(s.start_s, s.end_s, s.speaker) for s in data.segments] == [
        (1.0, 4.5, "Alice Exemple"),
        (5.0, 9.25, "Bruno Fictif"),
        (62.0, 65.0, "Alice Exemple"),
        (3600.0, 3602.0, "Chloé Test"),
    ]
    assert data.text == (
        "Alice Exemple : Bonjour à tous, on commence le point hebdo.\n"
        "Bruno Fictif : Le lot A est livré & la recette démarre lundi.\n"
        "Alice Exemple : On garde la date du 3 décembre.\n"
        "Chloé Test : Merci, à la semaine prochaine."
    )


def test_vtt_avec_orateurs_ignore_note_style_region() -> None:
    """Les blocs NOTE, STYLE et REGION ne donnent aucun segment (specs/06 §1.1)."""
    data = read_vtt(FIXTURES / "point-hebdo.vtt")
    assert len(data.segments) == 4


def test_vtt_sans_orateur_donne_segments_sans_orateur() -> None:
    """Sans balise `<v>` : orateur `None`, balises retirées (specs/06 §1.1)."""
    data = read_vtt(FIXTURES / "sans-orateur.vtt")
    assert [(s.start_s, s.end_s, s.speaker) for s in data.segments] == [
        (1.0, 3.0, None),
        (4.0, 7.5, None),
    ]
    assert data.text == "Ouverture de la séance par la présidente.\nPoint unique : budget 2027."


def test_vtt_corrompu_leve_value_error() -> None:
    """Un fichier qui ne commence pas par WEBVTT lève une erreur en français."""
    with pytest.raises(ValueError, match="Le fichier n'est pas au format WebVTT."):
        read_vtt(FIXTURES / "corrompu.vtt")


def test_vtt_sans_replique_leve_value_error() -> None:
    """Un fichier sans réplique lisible lève une erreur en français (specs/06 §1)."""
    with pytest.raises(ValueError, match="Aucune réplique lisible dans le fichier WebVTT."):
        read_vtt(FIXTURES / "sans-replique.vtt")


def _write_docx(path: Path, paragraphs: list[str]) -> None:
    """Un fichier `.docx` avec les paragraphes donnés."""
    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    document.save(str(path))


def test_docx_forme_supposee_donne_un_segment_par_prise_de_parole(tmp_path: Path) -> None:
    """Une ligne « orateur puis horodatage » ouvre un segment (specs/06 §1.2)."""
    path = tmp_path / "compte-rendu.docx"
    _write_docx(
        path,
        [
            "Alice Exemple 0:03",
            "Bonjour à tous.",
            "On commence par le budget.",
            "",
            "Bruno Fictif 1:15",
            "Le budget 2027 est validé.",
        ],
    )
    data = read_docx(path)
    assert [(s.start_s, s.end_s, s.speaker) for s in data.segments] == [
        (3.0, None, "Alice Exemple"),
        (75.0, None, "Bruno Fictif"),
    ]
    assert data.text == (
        "Alice Exemple : Bonjour à tous. On commence par le budget.\n"
        "Bruno Fictif : Le budget 2027 est validé."
    )


def test_docx_texte_libre_donne_un_segment_sans_orateur(tmp_path: Path) -> None:
    """Aucun en-tête reconnu : un seul segment, paragraphes joints par des retours à la ligne."""
    path = tmp_path / "libre.docx"
    _write_docx(path, ["Compte rendu libre du comité.", "", "Décision : on reporte le lot B."])
    data = read_docx(path)
    assert len(data.segments) == 1
    segment = data.segments[0]
    assert (segment.start_s, segment.end_s, segment.speaker) == (None, None, None)
    assert segment.text == "Compte rendu libre du comité.\nDécision : on reporte le lot B."


def test_docx_illisible_leve_value_error(tmp_path: Path) -> None:
    """Un fichier qui n'est pas un document Word lève une erreur en français."""
    path = tmp_path / "faux.docx"
    path.write_bytes(b"Ceci n'est pas un document Word.")
    with pytest.raises(ValueError, match="Fichier Word illisible."):
        read_docx(path)


def test_docx_sans_texte_leve_value_error(tmp_path: Path) -> None:
    """Un document sans paragraphe non vide lève une erreur en français (specs/06 §1)."""
    path = tmp_path / "vide.docx"
    _write_docx(path, ["", ""])
    with pytest.raises(ValueError, match="Le document Word ne contient aucun texte."):
        read_docx(path)


def test_modeles_du_port_sont_des_base_models() -> None:
    """`Segment` et `TranscriptData` sont les modèles de specs/02 §3.3."""
    segment = Segment(start_s=1.0, end_s=None, speaker="Alice Exemple", text="Bonjour.")
    data = TranscriptData(text=segment.text, segments=[segment])
    assert isinstance(segment, Segment)
    assert data.segments[0].model_dump() == {
        "start_s": 1.0,
        "end_s": None,
        "speaker": "Alice Exemple",
        "text": "Bonjour.",
    }
