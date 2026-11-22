"""Open-source model behind any self-hosted OpenAI-compatible chat endpoint (vLLM, Ollama, llama.cpp).

The HTTP client is standard library only; pdfplumber renders PDF pages to images for vision-capable models.
"""
import base64
import http.client
import io
import json
import logging
import urllib.error
import urllib.request
from typing import Sequence

from app.domain.contract import json_schema as json_schema_of_contract
from app.ingest.render import page_image
from app.llm.base import SYSTEM_PROMPT, LLMError, Page, parse_json

log = logging.getLogger(__name__)


class _BadRequest(LLMError):
    """HTTP 400 from the model server: usually a response_format it does not support."""


def _images(pages: Sequence[Page], limit: int) -> list[str]:
    """Data URIs; PDFs are rendered page by page (OpenAI-style chat accepts images only), first `limit` pages."""
    out = []
    for p in pages:
        if p.media_type != "application/pdf":
            out.append(f"data:{p.media_type};base64,{base64.b64encode(p.data).decode()}")
            continue
        import pdfplumber

        with pdfplumber.open(io.BytesIO(p.data)) as pdf:
            for page in pdf.pages[:limit]:
                buf = io.BytesIO()
                page_image(page, 110).convert("RGB").save(buf, "JPEG", quality=85)
                out.append(f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode()}")
    return out[:limit]


class OpenAICompatibleReader:
    def __init__(self, base_url: str, model: str, api_key: str, timeout_s: int, json_schema: bool = True,
                 max_images: int = 4):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model, self.api_key, self.timeout_s, self.max_images = model, api_key, timeout_s, max_images
        self.schema = {"type": "json_schema", "json_schema": {"name": "kontrakt", "schema": json_schema_of_contract()}} \
            if json_schema else None

    def _post(self, body: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:  # details stay in the log: the answer must not leak internals
            log.warning("model server answered %s: %r", e.code, e.read()[:300])
            raise (_BadRequest if e.code == 400 else LLMError)(f"Serwer modelu zwrócił błąd {e.code}.") from e
        except (OSError, http.client.HTTPException) as e:  # refused, reset, timeout, connection cut mid-answer
            log.warning("model server unreachable: %r", e)
            raise LLMError("Brak połączenia z serwerem modelu.") from e
        except ValueError as e:  # a proxy error page instead of JSON
            log.warning("model server answered with non-JSON: %r", e)
            raise LLMError("Serwer modelu zwrócił odpowiedź, która nie jest JSON-em.") from e

    def understand(self, lines_text: str, pages: Sequence[Page] = ()) -> dict:
        user = [{"type": "image_url", "image_url": {"url": u}} for u in _images(pages, self.max_images)]
        user = user + [{"type": "text", "text": lines_text}] if user else lines_text
        body = {"model": self.model, "temperature": 0, "response_format": self.schema or {"type": "json_object"},
                "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]}
        last = None
        for attempt in range(2):  # one retry: local models occasionally emit broken JSON (Ollama, form 7)
            try:
                data = self._post(body)
            except _BadRequest:
                if body["response_format"] is not self.schema:
                    raise
                body["response_format"] = {"type": "json_object"}  # server without JSON Schema support
                continue
            try:
                content = data["choices"][0]["message"]["content"] or ""
                cut = data["choices"][0].get("finish_reason") == "length"  # token limit: a retry would cut again
            except (KeyError, IndexError, TypeError, AttributeError) as e:
                log.warning("unexpected model server answer: %.300s", data)
                raise LLMError("Nieoczekiwana odpowiedź serwera modelu.") from e
            try:
                return parse_json(content, repair=cut or attempt == 1)
            except LLMError as e:
                last = e
        raise last or LLMError("Model nie zwrócił poprawnego JSON-a.")
