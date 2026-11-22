"""Application settings, read once from environment variables (secrets only at runtime, never in the image)."""
import os
from dataclasses import dataclass, fields

VISION_MODES = ("auto", "on", "off")
_TRUE, _FALSE = ("1", "true", "yes", "on"), ("0", "false", "no", "off")


@dataclass(frozen=True)
class Settings:
    """One field per environment variable of the same name in upper case (MAX_FILE_SIZE_MB, JWT_SECRET, ...)."""
    max_file_size_mb: int = 10
    max_pages: int = 40  # pages of one document; a scan is rendered and read by OCR page by page
    jwt_secret: str = ""  # required unless auth_disabled
    jwt_algorithm: str = "HS256"
    jwt_audience: str = ""
    auth_disabled: bool = False  # local development and tests only: the API then accepts requests without a token
    # Open-source model on an OpenAI-compatible server (vLLM, Ollama, llama.cpp); the task forbids API-only models.
    llm_base_url: str = ""  # e.g. http://host.docker.internal:18000/v1
    llm_model: str = "qwen3-vl"
    llm_api_key: str = ""  # only if the model server requires one
    llm_timeout_s: int = 300
    llm_max_images: int = 4  # page images per request (vLLM --limit-mm-per-prompt); first pages carry the header
    llm_json_schema: bool = True  # OpenAI-compatible servers: constrain output to the contract schema (vLLM)
    # Send the page itself (PDF or image) to the model next to the numbered lines:
    # auto = only when the text comes from OCR (photos, scans), where it lifted found fields 75% -> 92%;
    # on = always (no gain on PDFs with a text layer in our A/B); off = never.
    llm_vision: str = "auto"
    ocr_lang: str = "pol"
    soffice_bin: str = "soffice"
    convert_timeout_s: int = 120  # one LibreOffice conversion, and the OCR of one page

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @classmethod
    def from_env(cls) -> "Settings":
        """Unset or empty variables keep the defaults above; a value of the wrong type stops the start."""
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
