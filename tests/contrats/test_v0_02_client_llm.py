"""Tests de contrat du ticket V0-02, « Client du modèle local et garde de bouclage ».

Écrits par le relecteur avant la vague. Aucun test n'ouvre de connexion :
le serveur du modèle est remplacé par un `httpx.MockTransport`.

Le ticket ne dit pas dans quel module vivent `Message`, `LLMResult`,
`ConfigError`, `LLMError` et `assert_loopback`. Les tests les cherchent dans
les trois modules que le ticket crée, sans en imposer un.
"""

from __future__ import annotations

import base64
import importlib
import json
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

RACINE = Path(__file__).resolve().parents[2]
MODULES = ("cabinet.llm.client", "cabinet.llm.types", "cabinet.ports.llm")
URL_LOCALE = "http://127.0.0.1:8080/v1"
URL_DISTANTE = "https://api.exemple.test/v1"


def trouver(nom: str) -> Any:
    """Renvoie l'objet `nom` depuis le premier module du ticket qui le définit."""
    for module in MODULES:
        try:
            objet = getattr(importlib.import_module(module), nom, None)
        except ImportError:
            continue
        if objet is not None:
            return objet
    raise AssertionError(f"`{nom}` est introuvable dans {', '.join(MODULES)}")


def local_llm() -> Any:
    return importlib.import_module("cabinet.llm.client").LocalLLM


def message(role: str, content: str) -> Any:
    return trouver("Message")(role=role, content=content)


def reponse_du_serveur(contenu: str, avec_compteurs: bool = True) -> dict[str, Any]:
    corps: dict[str, Any] = {
        "id": "essai",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": contenu},
                "finish_reason": "stop",
            }
        ],
    }
    if avec_compteurs:
        corps["usage"] = {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}
    return corps


class FauxServeur:
    """Doublure du serveur du modèle : rejoue une liste de réponses."""

    def __init__(self, *reponses: Any) -> None:
        self.reponses = list(reponses)
        self.requetes: list[httpx.Request] = []

    def __call__(self, requete: httpx.Request) -> httpx.Response:
        self.requetes.append(requete)
        suivante = self.reponses[min(len(self.requetes), len(self.reponses)) - 1]
        if isinstance(suivante, Exception):
            raise suivante
        if isinstance(suivante, httpx.Response):
            return suivante
        return httpx.Response(200, json=reponse_du_serveur(suivante))

    def corps(self, rang: int = 0) -> dict[str, Any]:
        return json.loads(self.requetes[rang].content)


def client(serveur: Callable[[httpx.Request], httpx.Response]) -> Any:
    return local_llm()(URL_LOCALE, "qwen-local", transport=httpx.MockTransport(serveur))


class Verdict(BaseModel):
    """Schéma d'essai, avec un nom de champ reconnaissable dans la requête."""

    montant_total: int
    ok: bool


# --- Critère 1 -------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [URL_LOCALE, "http://localhost:4000/v1", "http://[::1]:8080/v1"],
)
def test_c1_adresses_de_bouclage_acceptees(url: str) -> None:
    assert trouver("assert_loopback")(url) is None


def test_c1_adresse_distante_leve_config_error() -> None:
    with pytest.raises(trouver("ConfigError")):
        trouver("assert_loopback")(URL_DISTANTE)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1.exemple.test/v1",
        "http://localhost.exemple.test:8080/v1",
        "http://exemple.test/127.0.0.1/v1",
        "http://192.168.1.10:8080/v1",
    ],
)
def test_c1_hote_non_local_refuse_meme_s_il_ressemble_a_une_adresse_locale(url: str) -> None:
    with pytest.raises(trouver("ConfigError")):
        trouver("assert_loopback")(url)


def test_c1_constructeur_refuse_une_adresse_distante() -> None:
    serveur = FauxServeur("inutile")
    with pytest.raises(trouver("ConfigError")):
        local_llm()(URL_DISTANTE, "qwen-local", transport=httpx.MockTransport(serveur))
    assert serveur.requetes == []


# --- Critère 2 -------------------------------------------------------------


def test_c2_variable_d_environnement_leve_la_garde(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CABINET_ALLOW_REMOTE_LLM", "1")
    assert trouver("assert_loopback")(URL_DISTANTE) is None
    serveur = FauxServeur("Bonjour")
    llm = local_llm()(URL_DISTANTE, "qwen-local", transport=httpx.MockTransport(serveur))
    assert llm.complete([message("user", "Dis bonjour.")]).text == "Bonjour"


def test_c2_toute_autre_valeur_laisse_la_garde(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CABINET_ALLOW_REMOTE_LLM", "0")
    with pytest.raises(trouver("ConfigError")):
        trouver("assert_loopback")(URL_DISTANTE)


# --- Critère 3 -------------------------------------------------------------


def test_c3_sans_schema_renvoie_le_texte_et_les_compteurs() -> None:
    serveur = FauxServeur("Bonjour")
    resultat = client(serveur).complete([message("user", "Dis bonjour.")])
    assert resultat.text == "Bonjour"
    assert resultat.parsed is None
    assert resultat.prompt_tokens == 12
    assert resultat.completion_tokens == 3


def test_c3_sans_compteurs_fournis_les_compteurs_restent_vides() -> None:
    def serveur(requete: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=reponse_du_serveur("Bonjour", avec_compteurs=False))

    resultat = client(serveur).complete([message("user", "Dis bonjour.")])
    assert resultat.text == "Bonjour"
    assert resultat.prompt_tokens is None
    assert resultat.completion_tokens is None


def test_c3_requete_conforme_au_contrat() -> None:
    serveur = FauxServeur("Bonjour")
    client(serveur).complete(
        [message("system", "Tu réponds en français."), message("user", "Dis bonjour.")]
    )
    assert len(serveur.requetes) == 1
    requete = serveur.requetes[0]
    assert requete.method == "POST"
    assert str(requete.url) == f"{URL_LOCALE}/chat/completions"
    corps = serveur.corps()
    assert corps["model"] == "qwen-local"
    assert corps["temperature"] == 0.2
    assert corps["messages"] == [
        {"role": "system", "content": "Tu réponds en français."},
        {"role": "user", "content": "Dis bonjour."},
    ]


# --- Critère 4 -------------------------------------------------------------


def test_c4_reponse_json_valide_renvoyee_dans_parsed() -> None:
    serveur = FauxServeur('{"montant_total": 42, "ok": true}')
    resultat = client(serveur).complete([message("user", "Donne le verdict.")], schema=Verdict)
    assert isinstance(resultat.parsed, Verdict)
    assert resultat.parsed.montant_total == 42
    assert resultat.parsed.ok is True


@pytest.mark.parametrize(
    "contenu",
    [
        '```json\n{"montant_total": 42, "ok": true}\n```',
        '```\n{"montant_total": 42, "ok": true}\n```',
        '\n```json\n{"montant_total": 42, "ok": true}\n```\n',
    ],
)
def test_c4_reponse_entouree_d_une_cloture_de_bloc_de_code(contenu: str) -> None:
    serveur = FauxServeur(contenu)
    resultat = client(serveur).complete([message("user", "Donne le verdict.")], schema=Verdict)
    assert isinstance(resultat.parsed, Verdict)
    assert resultat.parsed.montant_total == 42


def test_c4_message_systeme_en_tete_donne_le_schema() -> None:
    serveur = FauxServeur('{"montant_total": 42, "ok": true}')
    client(serveur).complete([message("user", "Donne le verdict.")], schema=Verdict)
    messages = serveur.corps()["messages"]
    assert messages[0]["role"] == "system"
    assert "montant_total" in messages[0]["content"]
    assert "JSON" in messages[0]["content"].upper()
    assert messages[-1] == {"role": "user", "content": "Donne le verdict."}


# --- Critère 5 -------------------------------------------------------------


def test_c5_deux_reponses_invalides_puis_une_valide_reussissent_en_trois_appels() -> None:
    serveur = FauxServeur(
        "ceci n'est pas du JSON",
        '{"autre_champ": 1}',
        '{"montant_total": 7, "ok": false}',
    )
    resultat = client(serveur).complete([message("user", "Donne le verdict.")], schema=Verdict)
    assert len(serveur.requetes) == 3
    assert isinstance(resultat.parsed, Verdict)
    assert resultat.parsed.montant_total == 7


def test_c5_la_relance_ajoute_l_erreur_au_dialogue() -> None:
    serveur = FauxServeur("ceci n'est pas du JSON", '{"montant_total": 7, "ok": false}')
    client(serveur).complete([message("user", "Donne le verdict.")], schema=Verdict)
    assert len(serveur.requetes) == 2
    assert len(serveur.corps(1)["messages"]) > len(serveur.corps(0)["messages"])


def test_c5_trois_reponses_invalides_levent_llm_error() -> None:
    serveur = FauxServeur("non", '{"autre_champ": 1}', "toujours pas")
    with pytest.raises(trouver("LLMError")):
        client(serveur).complete([message("user", "Donne le verdict.")], schema=Verdict)
    assert len(serveur.requetes) == 3


# --- Critère 6 -------------------------------------------------------------


def test_c6_image_envoyee_en_uri_de_donnees(tmp_path: Path) -> None:
    octets = b"\x89PNG\r\n\x1a\n" + b"image-synthetique"
    image = tmp_path / "app_capture.png"
    image.write_bytes(octets)
    serveur = FauxServeur("Vu.")
    client(serveur).complete([message("user", "Décris la capture.")], images=[image])

    dernier = serveur.corps()["messages"][-1]
    assert dernier["role"] == "user"
    contenu = dernier["content"]
    assert isinstance(contenu, list)
    assert contenu[0]["type"] == "text"
    assert contenu[0]["text"] == "Décris la capture."
    images = [element for element in contenu if element["type"] == "image_url"]
    assert len(images) == 1
    url = images[0]["image_url"]
    if isinstance(url, dict):
        url = url["url"]
    assert url.startswith("data:image/")
    assert ";base64," in url
    assert base64.b64decode(url.split(";base64,", 1)[1]) == octets


def test_c6_une_image_par_element(tmp_path: Path) -> None:
    chemins = []
    for rang in range(2):
        chemin = tmp_path / f"app_capture_{rang}.png"
        chemin.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes([rang]))
        chemins.append(chemin)
    serveur = FauxServeur("Vu.")
    client(serveur).complete([message("user", "Décris les captures.")], images=chemins)
    contenu = serveur.corps()["messages"][-1]["content"]
    assert [element["type"] for element in contenu] == ["text", "image_url", "image_url"]


# --- Critère 7 -------------------------------------------------------------


def test_c7_code_http_500_leve_llm_error() -> None:
    serveur = FauxServeur(httpx.Response(500, text="erreur interne"))
    with pytest.raises(trouver("LLMError")) as erreur:
        client(serveur).complete([message("user", "Dis bonjour.")])
    assert str(erreur.value).strip()


def test_c7_delai_depasse_leve_llm_error() -> None:
    serveur = FauxServeur(httpx.ReadTimeout("délai dépassé"))
    with pytest.raises(trouver("LLMError")) as erreur:
        client(serveur).complete([message("user", "Dis bonjour.")])
    assert str(erreur.value).strip()


# --- Critère 8 -------------------------------------------------------------


def test_c8_aucune_connexion_reseau_avec_le_transport_fourni(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def connexion_interdite(*args: object, **kwargs: object) -> None:
        raise AssertionError("le client a tenté d'ouvrir une connexion réseau")

    monkeypatch.setattr(socket.socket, "connect", connexion_interdite)
    monkeypatch.setattr(socket, "create_connection", connexion_interdite)
    serveur = FauxServeur('{"montant_total": 1, "ok": true}')
    resultat = client(serveur).complete([message("user", "Donne le verdict.")], schema=Verdict)
    assert resultat.parsed is not None


def test_c8_les_tests_de_l_executant_utilisent_un_transport_simule() -> None:
    fichier = RACINE / "tests" / "unit" / "test_llm_client.py"
    assert fichier.is_file(), "tests/unit/test_llm_client.py est demandé par le ticket"
    assert "MockTransport" in fichier.read_text(encoding="utf-8")


# --- Contrat, hors critères numérotés ---------------------------------------
# Forme des réponses de LM Studio avec Qwen3.8, relevée le 24.11.2026 :
# raisonnement dans un champ séparé, contenu précédé de deux retours à la ligne
# (specs/00 §4.2 et §4.3, specs/04 §3).


def reponse_avec_raisonnement(contenu: str) -> httpx.Response:
    corps = reponse_du_serveur(contenu)
    corps["choices"][0]["message"]["reasoning_content"] = "Je réfléchis avant de répondre."
    return httpx.Response(200, json=corps)


def test_contrat_seul_le_contenu_est_lu_sans_ses_espaces() -> None:
    serveur = FauxServeur(reponse_avec_raisonnement("\n\nBonjour"))
    resultat = client(serveur).complete([message("user", "Dis bonjour.")])
    assert resultat.text == "Bonjour"


def test_contrat_json_precede_de_retours_a_la_ligne_valide_du_premier_coup() -> None:
    serveur = FauxServeur(reponse_avec_raisonnement('\n\n{"montant_total": 42, "ok": true}'))
    resultat = client(serveur).complete([message("user", "Donne le verdict.")], schema=Verdict)
    assert isinstance(resultat.parsed, Verdict)
    assert len(serveur.requetes) == 1


def test_contrat_corps_sans_champ_superflu() -> None:
    serveur = FauxServeur('{"montant_total": 1, "ok": true}')
    client(serveur).complete([message("user", "Donne le verdict.")], schema=Verdict)
    assert set(serveur.corps()) == {"model", "messages", "temperature"}
