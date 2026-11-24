"""Client HTTP du modèle local et garde de bouclage (specs/04 §1 à §3)."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ValidationError

from cabinet.llm.types import ConfigError, LLMError, LLMResult, Message

_HOTES_LOCAUX = {"127.0.0.1", "localhost", "::1"}
_MIME_IMAGES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
_TEMPERATURE = 0.2
_NB_ESSAIS = 3


def assert_loopback(base_url: str) -> None:
    """Refuse toute adresse de modèle qui n'est pas une adresse de bouclage (specs/04 §2)."""
    if os.environ.get("CABINET_ALLOW_REMOTE_LLM") == "1":
        return None
    hote = urlsplit(base_url).hostname
    if hote in _HOTES_LOCAUX:
        return None
    recu = hote if hote else "adresse illisible"
    raise ConfigError(
        f"L'adresse du modèle doit être locale (127.0.0.1, localhost ou ::1). Reçu : {recu}."
    )


def _consigne_schema(schema: type[BaseModel]) -> str:
    """Message système qui demande une réponse en JSON seul, avec le schéma."""
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    return (
        "Réponds uniquement par un objet JSON conforme au schéma JSON ci-dessous. "
        "N'écris aucun texte avant ni après, et pas de bloc de code.\n"
        f"{schema_json}"
    )


def _sans_bloc_code(texte: str) -> str:
    """Retire une clôture de bloc de code autour d'une réponse JSON (specs/04 §3, étape 9)."""
    if not texte.startswith("```"):
        return texte
    lignes = texte.splitlines()
    if len(lignes) < 2 or not lignes[-1].strip().startswith("```"):
        return texte
    return "\n".join(lignes[1:-1]).strip()


def _defauts(erreur: ValidationError) -> str:
    """Liste au plus trois défauts de validation, sous la forme « champ : message »."""
    defauts = []
    for faute in erreur.errors()[:3]:
        champ = ".".join(str(part) for part in faute["loc"]) or "réponse"
        defauts.append(f"{champ} : {faute['msg']}")
    return "; ".join(defauts)


def _somme(valeurs: list[int | None]) -> int | None:
    """Somme des valeurs non nulles, ou None quand aucune ne l'est."""
    disponibles = [valeur for valeur in valeurs if valeur is not None]
    return sum(disponibles) if disponibles else None


class LocalLLM:
    """Client du modèle local, API de complétion de conversation d'OpenAI."""

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_s: float = 900,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        assert_loopback(base_url)
        self.base_url = base_url.removesuffix("/")
        self.model = model
        self.timeout_s = timeout_s
        self._client = httpx.Client(transport=transport, timeout=timeout_s)

    def complete(
        self,
        messages: list[Message],
        schema: type[BaseModel] | None = None,
        images: list[Path] | None = None,
    ) -> LLMResult:
        corps_messages: list[dict[str, object]] = [message.model_dump() for message in messages]
        if schema is not None:
            corps_messages.insert(0, {"role": "system", "content": _consigne_schema(schema)})
        if images:
            corps_messages = self._avec_images(corps_messages, images)

        compteurs_prompt: list[int | None] = []
        compteurs_compl: list[int | None] = []

        for essai in range(_NB_ESSAIS):
            texte, prompt_tokens, completion_tokens = self._appeler(corps_messages)
            compteurs_prompt.append(prompt_tokens)
            compteurs_compl.append(completion_tokens)

            if schema is None:
                return LLMResult(
                    text=texte,
                    prompt_tokens=_somme(compteurs_prompt),
                    completion_tokens=_somme(compteurs_compl),
                )

            try:
                parsed = schema.model_validate_json(_sans_bloc_code(texte))
            except ValidationError as erreur:
                if essai == _NB_ESSAIS - 1:
                    raise LLMError(
                        f"La réponse du modèle n'est pas un JSON conforme après 3 essais : "
                        f"{_defauts(erreur)}."
                    ) from erreur
                corps_messages.append({"role": "assistant", "content": texte})
                corps_messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Ta réponse n'est pas un objet JSON conforme au schéma : "
                            f"{_defauts(erreur)}. Renvoie uniquement l'objet JSON corrigé."
                        ),
                    }
                )
            else:
                return LLMResult(
                    text=texte,
                    parsed=parsed,
                    prompt_tokens=_somme(compteurs_prompt),
                    completion_tokens=_somme(compteurs_compl),
                )

        # Inatteignable : la boucle renvoie ou lève à chaque essai.
        raise LLMError("La réponse du modèle n'est pas un JSON conforme après 3 essais.")

    def _appeler(
        self, corps_messages: list[dict[str, object]]
    ) -> tuple[str, int | None, int | None]:
        corps = {"model": self.model, "messages": corps_messages, "temperature": _TEMPERATURE}
        try:
            reponse = self._client.post(f"{self.base_url}/chat/completions", json=corps)
        except httpx.TimeoutException as erreur:
            raise LLMError(
                f"Le modèle local n'a pas répondu en {self.timeout_s} secondes."
            ) from erreur
        except httpx.HTTPError as erreur:
            raise LLMError(f"Le serveur du modèle est injoignable à {self.base_url}.") from erreur

        if reponse.status_code >= 400:
            raise LLMError(f"Le modèle local a répondu par une erreur HTTP {reponse.status_code}.")

        try:
            donnees = reponse.json()
            contenu = donnees["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as erreur:
            raise LLMError("Le modèle local a renvoyé une réponse inattendue.") from erreur

        texte = contenu.strip() if isinstance(contenu, str) else ""
        usage = donnees.get("usage")
        prompt_tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        completion_tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
        return texte, prompt_tokens, completion_tokens

    def _avec_images(
        self, corps_messages: list[dict[str, object]], images: list[Path]
    ) -> list[dict[str, object]]:
        for index in range(len(corps_messages) - 1, -1, -1):
            if corps_messages[index]["role"] != "user":
                continue
            elements: list[dict[str, object]] = [
                {"type": "text", "text": corps_messages[index]["content"]}
            ]
            for image in images:
                elements.append(
                    {"type": "image_url", "image_url": {"url": self._uri_de_donnees(image)}}
                )
            corps_messages[index] = {**corps_messages[index], "content": elements}
            return corps_messages
        raise ValueError("Aucun message utilisateur auquel rattacher les images.")

    def _uri_de_donnees(self, image: Path) -> str:
        mime = _MIME_IMAGES.get(image.suffix.lower())
        if mime is None:
            raise ValueError(f"Extension d'image non prise en charge : {image.suffix or 'aucune'}.")
        donnees = base64.b64encode(image.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{donnees}"
