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

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
_MIME_IMAGES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
_TEMPERATURE = 0.2
_MAX_ATTEMPTS = 3


def assert_loopback(base_url: str) -> None:
    """Refuse toute adresse de modèle qui n'est pas une adresse de bouclage (specs/04 §2)."""
    if os.environ.get("CABINET_ALLOW_REMOTE_LLM") == "1":
        return None
    host = urlsplit(base_url).hostname
    if host in _LOCAL_HOSTS:
        return None
    received = host if host else "adresse illisible"
    raise ConfigError(
        f"L'adresse du modèle doit être locale (127.0.0.1, localhost ou ::1). Reçu : {received}."
    )


def _schema_instruction(schema: type[BaseModel]) -> str:
    """Message système qui demande une réponse en JSON seul, avec le schéma."""
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    return (
        "Réponds uniquement par un objet JSON conforme au schéma JSON ci-dessous. "
        "N'écris aucun texte avant ni après, et pas de bloc de code.\n"
        f"{schema_json}"
    )


def _strip_code_block(text: str) -> str:
    """Retire une clôture de bloc de code autour d'une réponse JSON (specs/04 §3, étape 9)."""
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if len(lines) < 2 or not lines[-1].strip().startswith("```"):
        return text
    return "\n".join(lines[1:-1]).strip()


def _defects(error: ValidationError) -> str:
    """Liste au plus trois défauts de validation, sous la forme « champ : message »."""
    defects = []
    for flaw in error.errors()[:3]:
        field = ".".join(str(part) for part in flaw["loc"]) or "réponse"
        defects.append(f"{field} : {flaw['msg']}")
    return "; ".join(defects)


def _total(values: list[int | None]) -> int | None:
    """Somme des valeurs non nulles, ou None quand aucune ne l'est."""
    available = [value for value in values if value is not None]
    return sum(available) if available else None


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
        body_messages = self._initial_messages(messages, schema, images)
        prompt_counts: list[int | None] = []
        completion_counts: list[int | None] = []

        for attempt in range(_MAX_ATTEMPTS):
            text, prompt_tokens, completion_tokens = self._post(body_messages)
            prompt_counts.append(prompt_tokens)
            completion_counts.append(completion_tokens)

            if schema is None:
                return self._result(text, None, prompt_counts, completion_counts)

            try:
                parsed = schema.model_validate_json(_strip_code_block(text))
            except ValidationError as error:
                if attempt == _MAX_ATTEMPTS - 1:
                    raise LLMError(
                        f"La réponse du modèle n'est pas un JSON conforme après 3 essais : "
                        f"{_defects(error)}."
                    ) from error
                self._retry(body_messages, text, error)
            else:
                return self._result(text, parsed, prompt_counts, completion_counts)

    def _initial_messages(
        self,
        messages: list[Message],
        schema: type[BaseModel] | None,
        images: list[Path] | None,
    ) -> list[dict[str, object]]:
        """Messages de départ : conversion, consigne de schéma en tête, images rattachées."""
        body_messages: list[dict[str, object]] = [message.model_dump() for message in messages]
        if schema is not None:
            body_messages.insert(0, {"role": "system", "content": _schema_instruction(schema)})
        if images:
            body_messages = self._with_images(body_messages, images)
        return body_messages

    def _result(
        self,
        text: str,
        parsed: BaseModel | None,
        prompt_counts: list[int | None],
        completion_counts: list[int | None],
    ) -> LLMResult:
        """Résultat avec les compteurs additionnés sur tous les essais."""
        return LLMResult(
            text=text,
            parsed=parsed,
            prompt_tokens=_total(prompt_counts),
            completion_tokens=_total(completion_counts),
        )

    def _retry(
        self, body_messages: list[dict[str, object]], text: str, error: ValidationError
    ) -> None:
        """Ajoute la réponse reçue et la demande de correction au dialogue
        (specs/04 §3, étape 11)."""
        body_messages.append({"role": "assistant", "content": text})
        body_messages.append(
            {
                "role": "user",
                "content": (
                    f"Ta réponse n'est pas un objet JSON conforme au schéma : "
                    f"{_defects(error)}. Renvoie uniquement l'objet JSON corrigé."
                ),
            }
        )

    def _post(
        self, body_messages: list[dict[str, object]]
    ) -> tuple[str, int | None, int | None]:
        body = {"model": self.model, "messages": body_messages, "temperature": _TEMPERATURE}
        try:
            response = self._client.post(f"{self.base_url}/chat/completions", json=body)
        except httpx.TimeoutException as error:
            raise LLMError(
                f"Le modèle local n'a pas répondu en {self.timeout_s} secondes."
            ) from error
        except httpx.HTTPError as error:
            raise LLMError(f"Le serveur du modèle est injoignable à {self.base_url}.") from error

        if response.status_code >= 400:
            raise LLMError(f"Le modèle local a répondu par une erreur HTTP {response.status_code}.")

        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise LLMError("Le modèle local a renvoyé une réponse inattendue.") from error

        text = content.strip() if isinstance(content, str) else ""
        usage = data.get("usage")
        prompt_tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        completion_tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
        return text, prompt_tokens, completion_tokens

    def _with_images(
        self, body_messages: list[dict[str, object]], images: list[Path]
    ) -> list[dict[str, object]]:
        for index in range(len(body_messages) - 1, -1, -1):
            if body_messages[index]["role"] != "user":
                continue
            elements: list[dict[str, object]] = [
                {"type": "text", "text": body_messages[index]["content"]}
            ]
            for image in images:
                elements.append(
                    {"type": "image_url", "image_url": {"url": self._data_uri(image)}}
                )
            body_messages[index] = {**body_messages[index], "content": elements}
            return body_messages
        raise ValueError("Aucun message utilisateur auquel rattacher les images.")

    def _data_uri(self, image: Path) -> str:
        mime = _MIME_IMAGES.get(image.suffix.lower())
        if mime is None:
            raise ValueError(f"Extension d'image non prise en charge : {image.suffix or 'aucune'}.")
        data = base64.b64encode(image.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{data}"
