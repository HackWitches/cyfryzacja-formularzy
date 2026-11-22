"""Application settings, read once from environment variables.

Secrets come in at runtime only, never baked into the image.
"""

import os
from dataclasses import dataclass, fields

VISION_MODES = ("auto", "on", "off")
_TRUE, _FALSE = ("1", "true", "yes", "on"), ("0", "false", "no", "off")


@dataclass(frozen=True)
class Settings:
    """One field per environment variable with the same name in upper case, e.g. JWT_SECRET."""

    max_file_size_mb: int = 10
    max_pages: int = 40  # per document; scans are rendered and OCR'd page by page, so this bounds the work
    jwt_secret: str = ""  # required unless auth_disabled
    jwt_algorithm: str = "HS256"
    jwt_audience: str = ""
    auth_disabled: bool = False  # local development and tests only: the API then accepts requests without a token
    # Open-source model on an OpenAI-compatible server (vLLM, Ollama, llama.cpp). COI rule: no API-only models.
    llm_base_url: str = ""  # OpenAI-compatible server, e.g. http://ollama:11434/v1
    llm_model: str = "qwen3-vl"
    llm_api_key: str = ""  # only if the model server requires one
    llm_timeout_s: int = 300
    llm_max_images: int = 4  # page images per request (vLLM --limit-mm-per-prompt); first pages carry the header
    llm_json_schema: bool = True  # constrain output to the contract schema; unsupported servers fall back to JSON mode
    llm_system_role: bool = True  # false: the prompt goes in the user message (models without system messages)
    # Send the page itself (PDF or image) to the model next to the numbered lines.
    # auto: only for OCR text (photos, scans), where it raised found fields from 75% to 92%.
    # on: always (no gain on PDFs with a text layer in our A/B test). off: never.
    llm_vision: str = "auto"
    ocr_lang: str = "pol"
    soffice_bin: str = "soffice"
    convert_timeout_s: int = 120  # one LibreOffice conversion, and the OCR of one page

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @classmethod
    def from_env(cls) -> "Settings":
        """Read the environment; unset or empty variables keep the defaults, a malformed value raises."""
        values = {}
        for f in fields(cls):
            raw = os.environ.get(f.name.upper(), "").strip()
            if raw:
                values[f.name] = _parse(f.name.upper(), raw, type(f.default))
        return cls(**values)

    def __post_init__(self):
        if self.llm_vision not in VISION_MODES:
            raise ValueError(f"LLM_VISION must be one of {', '.join(VISION_MODES)}, got {self.llm_vision!r}")


def _parse(name: str, raw: str, kind: type):
    if kind is bool:
        if raw.lower() not in _TRUE + _FALSE:
            raise ValueError(f"{name} must be true or false, got {raw!r}")
        return raw.lower() in _TRUE
    if kind is int:
        if not raw.isdigit():
            raise ValueError(f"{name} must be a whole number, got {raw!r}")
        return int(raw)
    if name == "LLM_VISION":  # true/false are accepted as on/off
        return {"true": "on", "1": "on", "false": "off", "0": "off"}.get(raw.lower(), raw.lower())
    return raw
