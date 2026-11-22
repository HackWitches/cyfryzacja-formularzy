"""What the pipeline needs from a language model: numbered form lines in, the contract (a dict) out."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

SYSTEM_PROMPT = (Path(__file__).parent / "prompt.txt").read_text(encoding="utf-8")


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class Page:
    """The form itself for a vision-capable model: a PDF or an image."""

    media_type: str  # application/pdf | image/jpeg | image/png
    data: bytes


class FormReader(Protocol):
    def understand(self, lines_text: str, pages: Sequence[Page] = ()) -> dict: ...


def parse_json(text: str, repair: bool = False) -> dict:
    """Parse the model's one JSON object, tolerating code fences or prose around it.

    With `repair`, a truncated contract is closed after its last complete object.
    """
    if not isinstance(text, str):
        raise LLMError("Model nie zwrócił tekstu.")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise LLMError("Model nie zwrócił obiektu JSON.")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        data = _close_truncated(text[start:]) if repair else None
        if data is None:
            raise LLMError("Model zwrócił niepoprawny JSON.") from e
    if not isinstance(data, dict):
        raise LLMError("Model zwrócił JSON, który nie jest obiektem.")
    return data


# A contract cut by the token limit ends inside strony -> sekcje -> pola.
# Closing it after the last complete object keeps every finished field instead of failing the request.
TAILS = ("]}", "]}]}", "]}]}]}", "}]}]}]}")
MAX_TRIES = 400  # last '}' positions tried; each try is one json.loads of the answer


def _close_truncated(text: str) -> dict | None:
    ends = [i for i, ch in enumerate(text) if ch == "}"][::-1][:MAX_TRIES]
    for i in ends:
        for tail in TAILS:
            try:
                data = json.loads(text[: i + 1] + tail)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and isinstance(data.get("strony"), list):
                data["_ucieta_odpowiedz"] = True
                return data
    return None
