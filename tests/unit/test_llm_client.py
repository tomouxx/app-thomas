"""Tests unitaires du client du modèle local et de la garde de bouclage.

Complètent les tests de contrat (`tests/contrats/test_v0_02_client_llm.py`)
sur les cas qu'ils ne couvrent pas. Aucun test n'ouvre de connexion réseau :
le serveur est remplacé par un `httpx.MockTransport`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel
from typer.testing import CliRunner

import cabinet.cli as cli_module
from cabinet.cli import Ping
from cabinet.cli import app as cli_app
from cabinet.llm.client import LocalLLM, assert_loopback
from cabinet.llm.types import ConfigError, LLMError, LLMResult, Message

URL_LOCALE = "http://127.0.0.1:8080/v1"
URL_DISTANTE = "https://api.exemple.test/v1"


class Verdict(BaseModel):
    montant_total: int
    ok: bool


@pytest.fixture(autouse=True)
def garde_non_levee(monkeypatch: pytest.MonkeyPatch) -> None:
    """La garde de bouclage n'est jamais levée par l'environnement des tests."""
    monkeypatch.delenv("CABINET_ALLOW_REMOTE_LLM", raising=False)


def reponse_du_serveur(contenu: Any, avec_compteurs: bool = True) -> dict[str, Any]:
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


def client(serveur: Callable[[httpx.Request], httpx.Response]) -> LocalLLM:
    return LocalLLM(URL_LOCALE, "qwen-local", transport=httpx.MockTransport(serveur))


# --- Garde de bouclage ------------------------------------------------------


def test_garde_refuse_une_adresse_sans_hote() -> None:
    with pytest.raises(ConfigError, match="adresse illisible"):
        assert_loopback("pas-une-adresse")


def test_garde_refuse_une_adresse_sans_hote_meme_avec_le_port() -> None:
    with pytest.raises(ConfigError, match="adresse illisible"):
        assert_loopback("http:///v1")


def test_garde_seule_la_valeur_exacte_1_leve(monkeypatch: pytest.MonkeyPatch) -> None:
    for valeur in ("0", "true", "2", ""):
        monkeypatch.setenv("CABINET_ALLOW_REMOTE_LLM", valeur)
        with pytest.raises(ConfigError):
            assert_loopback(URL_DISTANTE)


def test_garde_message_nomme_l_hote_recu() -> None:
    with pytest.raises(ConfigError, match="Reçu : api\\.exemple\\.test"):
        assert_loopback(URL_DISTANTE)


# --- Constructeur -----------------------------------------------------------


def test_constructeur_garde_le_nom_du_modele_et_l_adresse() -> None:
    llm = client(FauxServeur("inutile"))
    assert llm.model == "qwen-local"
    assert llm.base_url == URL_LOCALE


def test_constructeur_retire_la_barre_oblique_finale() -> None:
    serveur = FauxServeur("Bonjour")
    llm = LocalLLM(f"{URL_LOCALE}/", "qwen-local", transport=httpx.MockTransport(serveur))
    llm.complete([Message(role="user", content="Dis bonjour.")])
    assert str(serveur.requetes[0].url) == f"{URL_LOCALE}/chat/completions"


# --- Lecture de la réponse --------------------------------------------------


def test_contenu_nul_vaut_chaine_vide() -> None:
    def serveur(requete: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=reponse_du_serveur(None))

    assert client(serveur).complete([Message(role="user", content="Bonjour.")]).text == ""


def test_reponse_sans_choices_leve_llm_error() -> None:
    def serveur(requete: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "essai", "choices": []})

    with pytest.raises(LLMError, match="réponse inattendue"):
        client(serveur).complete([Message(role="user", content="Bonjour.")])


def test_corps_non_json_leve_llm_error() -> None:
    def serveur(requete: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ceci n'est pas du JSON")

    with pytest.raises(LLMError, match="réponse inattendue"):
        client(serveur).complete([Message(role="user", content="Bonjour.")])


def test_erreur_http_nomme_le_code() -> None:
    serveur = FauxServeur(httpx.Response(503, text="indisponible"))
    with pytest.raises(LLMError, match="erreur HTTP 503"):
        client(serveur).complete([Message(role="user", content="Bonjour.")])


def test_delai_depasse_nomme_le_delai() -> None:
    def serveur(requete: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("délai dépassé")

    llm = LocalLLM(URL_LOCALE, "qwen-local", timeout_s=1.5, transport=httpx.MockTransport(serveur))
    with pytest.raises(LLMError, match="n'a pas répondu en 1.5 secondes"):
        llm.complete([Message(role="user", content="Bonjour.")])


def test_serveur_injoignable_leve_llm_error() -> None:
    def serveur(requete: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refusé")

    with pytest.raises(LLMError, match="injoignable"):
        client(serveur).complete([Message(role="user", content="Bonjour.")])


# --- Relances avec schéma ---------------------------------------------------


def test_relance_renvoie_la_reponse_recue_en_message_assistant() -> None:
    serveur = FauxServeur("non", '{"montant_total": 7, "ok": false}')
    client(serveur).complete([Message(role="user", content="Donne le verdict.")], schema=Verdict)
    messages = serveur.corps(1)["messages"]
    assert messages[-2] == {"role": "assistant", "content": "non"}
    assert messages[-1]["role"] == "user"
    assert "JSON" in messages[-1]["content"]


def test_trois_reponses_invalides_nomment_les_defauts() -> None:
    serveur = FauxServeur("non", '{"autre_champ": 1}', "toujours pas")
    with pytest.raises(LLMError, match="après 3 essais"):
        client(serveur).complete([Message(role="user", content="Donne le verdict.")], schema=Verdict)


def test_compteurs_somme_des_essais_qui_en_ont() -> None:
    requetes: list[httpx.Request] = []

    def serveur(requete: httpx.Request) -> httpx.Response:
        requetes.append(requete)
        if len(requetes) == 1:
            return httpx.Response(200, json=reponse_du_serveur("non", avec_compteurs=False))
        return httpx.Response(200, json=reponse_du_serveur('{"montant_total": 7, "ok": false}'))

    resultat = client(serveur).complete([Message(role="user", content="Donne le verdict.")], schema=Verdict)
    assert len(requetes) == 2
    assert resultat.prompt_tokens == 12
    assert resultat.completion_tokens == 3


def test_compteurs_nuls_quand_aucun_essai_n_en_a() -> None:
    def serveur(requete: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=reponse_du_serveur("Bonjour", avec_compteurs=False))

    resultat = client(serveur).complete([Message(role="user", content="Dis bonjour.")])
    assert resultat.prompt_tokens is None
    assert resultat.completion_tokens is None


# --- Images -----------------------------------------------------------------


def test_image_extension_majuscule_est_acceptee(tmp_path: Path) -> None:
    image = tmp_path / "app_capture.JPG"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    serveur = FauxServeur("Vu.")
    client(serveur).complete([Message(role="user", content="Décris la capture.")], images=[image])
    contenu = serveur.corps()["messages"][-1]["content"]
    url = contenu[1]["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")


def test_image_extension_inconnue_leve_value_error(tmp_path: Path) -> None:
    image = tmp_path / "capture.gif"
    image.write_bytes(b"GIF89a")
    with pytest.raises(ValueError, match="non prise en charge"):
        client(FauxServeur("Vu.")).complete(
            [Message(role="user", content="Décris la capture.")], images=[image]
        )


def test_images_sans_message_utilisateur_levent_value_error(tmp_path: Path) -> None:
    image = tmp_path / "app_capture.png"  # jamais lu : l'erreur précède la lecture
    with pytest.raises(ValueError, match="Aucun message utilisateur"):
        client(FauxServeur("Vu.")).complete(
            [Message(role="system", content="Tu décris des captures.")], images=[image]
        )


def test_images_rattachees_au_dernier_message_utilisateur(tmp_path: Path) -> None:
    image = tmp_path / "app_capture.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    serveur = FauxServeur("Vu.")
    client(serveur).complete(
        [
            Message(role="user", content="Première question."),
            Message(role="assistant", content="Une réponse."),
            Message(role="user", content="Deuxième question."),
        ],
        images=[image],
    )
    messages = serveur.corps()["messages"]
    assert isinstance(messages[0]["content"], str)
    contenu = messages[2]["content"]
    assert isinstance(contenu, list)
    assert contenu[0] == {"type": "text", "text": "Deuxième question."}


# --- Commande `cabinet llm-test` --------------------------------------------


def test_llm_test_affiche_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    class FauxLocalLLM:
        def __init__(self, base_url: str = "", model: str = "", timeout_s: float = 0) -> None:
            pass

        def complete(self, messages: list[Message], schema=None, images=None) -> LLMResult:
            return LLMResult(text='{"ok": true}', parsed=Ping(ok=True))

    monkeypatch.setattr(cli_module, "LocalLLM", FauxLocalLLM)
    resultat = CliRunner().invoke(cli_app, ["llm-test"])
    assert resultat.exit_code == 0
    assert "OK" in resultat.output


def test_llm_test_utilise_les_valeurs_par_defaut(monkeypatch: pytest.MonkeyPatch) -> None:
    construits: list[tuple[str, str, float]] = []

    class FauxLocalLLM:
        def __init__(self, base_url: str = "", model: str = "", timeout_s: float = 0) -> None:
            construits.append((base_url, model, timeout_s))

        def complete(self, messages: list[Message], schema=None, images=None) -> LLMResult:
            return LLMResult(text='{"ok": true}', parsed=Ping(ok=True))

    monkeypatch.setattr(cli_module, "LocalLLM", FauxLocalLLM)
    resultat = CliRunner().invoke(cli_app, ["llm-test"])
    assert resultat.exit_code == 0
    assert construits[0] == ("http://127.0.0.1:8080/v1", "qwen-local", 900)


def test_llm_test_echec_de_modele_sort_1(monkeypatch: pytest.MonkeyPatch) -> None:
    class FauxLocalLLM:
        def __init__(self, base_url: str = "", model: str = "", timeout_s: float = 0) -> None:
            pass

        def complete(self, messages: list[Message], schema=None, images=None) -> LLMResult:
            raise LLMError("Le modèle local a répondu par une erreur HTTP 500.")

    monkeypatch.setattr(cli_module, "LocalLLM", FauxLocalLLM)
    resultat = CliRunner().invoke(cli_app, ["llm-test"])
    assert resultat.exit_code == 1
    assert "erreur HTTP 500" in (resultat.stderr or "")


def test_llm_test_ok_faux_sort_1(monkeypatch: pytest.MonkeyPatch) -> None:
    class FauxLocalLLM:
        def __init__(self, base_url: str = "", model: str = "", timeout_s: float = 0) -> None:
            pass

        def complete(self, messages: list[Message], schema=None, images=None) -> LLMResult:
            return LLMResult(text='{"ok": false}', parsed=Ping(ok=False))

    monkeypatch.setattr(cli_module, "LocalLLM", FauxLocalLLM)
    resultat = CliRunner().invoke(cli_app, ["llm-test"])
    assert resultat.exit_code == 1
