"""Application settings, read once from environment variables (secrets only at runtime, never in the image)."""
import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    max_file_size_mb: int = 10
    jwt_secret: str = ""  # empty = authentication off (local development only)
    jwt_algorithm: str = "HS256"
    jwt_audience: str = ""
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
    convert_timeout_s: int = 120

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        return cls(
            max_file_size_mb=_int("MAX_FILE_SIZE_MB", cls.max_file_size_mb),
            jwt_secret=e("JWT_SECRET", ""),
            jwt_algorithm=e("JWT_ALGORITHM", cls.jwt_algorithm),
            jwt_audience=e("JWT_AUDIENCE", ""),
            llm_base_url=e("LLM_BASE_URL", ""),
            llm_model=e("LLM_MODEL", cls.llm_model),
            llm_api_key=e("LLM_API_KEY", ""),
            llm_timeout_s=_int("LLM_TIMEOUT_S", cls.llm_timeout_s),
            llm_max_images=_int("LLM_MAX_IMAGES", cls.llm_max_images),
            llm_json_schema=e("LLM_JSON_SCHEMA", "true").lower() not in ("0", "false", "no"),
            llm_vision={"true": "on", "1": "on", "false": "off", "0": "off"}.get(
                e("LLM_VISION", "auto").lower(), e("LLM_VISION", "auto").lower()),
            ocr_lang=e("OCR_LANG", cls.ocr_lang),
            soffice_bin=e("SOFFICE_BIN", cls.soffice_bin),
            convert_timeout_s=_int("CONVERT_TIMEOUT_S", cls.convert_timeout_s),
        )
