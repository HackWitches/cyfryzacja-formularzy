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
    """HTTP 400 from the model server: usually a parameter it does not support."""

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = detail  # the server's own words, for choosing what to drop; never shown to the user


def _images(pages: Sequence[Page], limit: int) -> list[str]:
    """Image data URIs for the first `limit` pages; PDFs are rendered, as OpenAI-style chat takes images only."""
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
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        timeout_s: int,
        json_schema: bool = True,
        max_images: int = 4,
        system_role: bool = True,
    ):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model, self.api_key, self.timeout_s, self.max_images = model, api_key, timeout_s, max_images
        self.system_role = system_role
        self.schema = (
            {"type": "json_schema", "json_schema": {"name": "kontrakt", "schema": json_schema_of_contract()}}
            if json_schema
            else None
        )
        # Optional request parameters. A server that refuses one with HTTP 400 gets the request without it,
        # and later requests skip it too (e.g. a server without temperature or with strict-only schemas).
        self.options = {"temperature": 0, "response_format": self.schema or {"type": "json_object"}}

    def _drop_refused(self, detail: str) -> bool:
        """Remove the option the server refused; False when nothing is left to drop."""
        if "temperature" in self.options and "temperature" in detail.lower():
            del self.options["temperature"]
        elif self.options.get("response_format") is self.schema and self.schema:
            self.options["response_format"] = {"type": "json_object"}  # no JSON Schema support
        elif "response_format" in self.options:
            del self.options["response_format"]  # no JSON mode either: the prompt alone asks for JSON
        else:
            return False
        log.warning("model server refused a parameter, retrying without it: %s", detail[:200])
        return True

    def _post(self, body: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:  # details stay in the log: the answer must not leak internals
            detail = e.read()[:300].decode("utf-8", "replace")
            log.warning("model server answered %s: %s", e.code, detail)
            if e.code == 400:
                raise _BadRequest(f"Serwer modelu zwrócił błąd {e.code}.", detail) from e
            raise LLMError(f"Serwer modelu zwrócił błąd {e.code}.") from e
        except (OSError, http.client.HTTPException) as e:  # refused, reset, timeout, connection cut mid-answer
            log.warning("model server unreachable: %r", e)
            raise LLMError("Brak połączenia z serwerem modelu.") from e
        except ValueError as e:  # a proxy error page instead of JSON
            log.warning("model server answered with non-JSON: %r", e)
            raise LLMError("Serwer modelu zwrócił odpowiedź, która nie jest JSON-em.") from e

    def understand(self, lines_text: str, pages: Sequence[Page] = ()) -> dict:
        text = lines_text if self.system_role else f"{SYSTEM_PROMPT}\n\n{lines_text}"
        user = [{"type": "image_url", "image_url": {"url": u}} for u in _images(pages, self.max_images)]
        user = user + [{"type": "text", "text": text}] if user else text
        messages = ([{"role": "system", "content": SYSTEM_PROMPT}] if self.system_role else []) + [
            {"role": "user", "content": user}
        ]
        last, attempt = None, 0
        while attempt < 2:  # one retry: local models (e.g. Ollama) sometimes emit broken JSON
            try:
                data = self._post({"model": self.model, "messages": messages, **self.options})
            except _BadRequest as e:
                if not self._drop_refused(e.detail):
                    raise
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
                last, attempt = e, attempt + 1
        raise last or LLMError("Model nie zwrócił poprawnego JSON-a.")
