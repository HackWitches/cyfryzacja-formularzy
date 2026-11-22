from app.config import Settings
from app.llm.base import FormReader, LLMError, Page
from app.llm.openai_compat import OpenAICompatibleReader


def make_reader(s: Settings) -> FormReader:
    """Open-weights model behind any OpenAI-compatible server (Ollama, vLLM, Eden AI): LLM_BASE_URL + LLM_MODEL."""
    if not s.llm_base_url:
        raise ValueError("LLM_BASE_URL is not set: point it at the model server, e.g. http://host:8000/v1")
    return OpenAICompatibleReader(
        s.llm_base_url,
        s.llm_model,
        s.llm_api_key,
        s.llm_timeout_s,
        s.llm_json_schema,
        s.llm_max_images,
        s.llm_system_role,
    )


__all__ = ["FormReader", "LLMError", "Page", "make_reader"]
