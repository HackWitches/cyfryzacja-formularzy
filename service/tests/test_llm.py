"""The model client against a fake HTTP layer: every failure of the model server is an LLMError (HTTP 502)."""
import http.client
import io
import json
import urllib.error
import urllib.request

import pytest

from app.domain import contract as K
from app.llm import LLMError
from app.llm.base import SYSTEM_PROMPT
from app.llm.openai_compat import OpenAICompatibleReader

CONTRACT = {"tytul": "Wniosek o test", "strony": []}


class Response:
    def __init__(self, body=b"", error=None):
        self.body, self.error = body, error

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        if self.error:
            raise self.error
        return self.body


def answer(content, finish_reason="stop") -> Response:
    choice = {"message": {"content": content}, "finish_reason": finish_reason}
    return Response(json.dumps({"choices": [choice]}).encode())


def reader(monkeypatch, *responses):
    """A reader whose HTTP calls return (or raise) the given responses in turn; also returns the request bodies."""
    queue, sent = list(responses), []

    def urlopen(request, timeout=None):
        sent.append(json.loads(request.data))
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return OpenAICompatibleReader("http://model/v1", "qwen3-vl", "", 5), sent


def bad_request():
    return urllib.error.HTTPError("http://model/v1", 400, "Bad Request", {}, io.BytesIO(b"response_format unsupported"))


def test_answer_is_parsed(monkeypatch):
    r, sent = reader(monkeypatch, answer("```json\n" + json.dumps(CONTRACT) + "\n```"))
    assert r.understand("L001 x") == CONTRACT
    assert sent[0]["response_format"]["type"] == "json_schema" and sent[0]["temperature"] == 0


@pytest.mark.parametrize("failure", [
    Response(b"<html>502 Bad Gateway</html>"),              # a proxy page instead of JSON
    Response(error=ConnectionResetError("reset")),          # the tunnel dropped while the model was answering
    Response(error=http.client.IncompleteRead(b"{")),
    urllib.error.URLError("connection refused"),
    TimeoutError("timed out"),
    Response(b'{"choices": []}'),
    answer(None), answer(None),                             # an empty answer, twice
])
def test_every_model_server_failure_is_an_llm_error(monkeypatch, failure):
    r, _ = reader(monkeypatch, failure, failure)
    with pytest.raises(LLMError) as e:
        r.understand("L001 x")
    assert "model/v1" not in str(e.value) and "reset" not in str(e.value)  # internals stay in the log


def test_server_without_json_schema_gets_a_plain_json_request(monkeypatch):
    r, sent = reader(monkeypatch, bad_request(), answer(json.dumps(CONTRACT)))
    assert r.understand("L001 x") == CONTRACT
    assert [b["response_format"]["type"] for b in sent] == ["json_schema", "json_object"]
    r, _ = reader(monkeypatch, bad_request(), bad_request())
    with pytest.raises(LLMError):
        r.understand("L001 x")


def test_broken_json_is_retried_once_and_a_cut_answer_is_closed(monkeypatch):
    r, sent = reader(monkeypatch, answer('{"tytul": '), answer(json.dumps(CONTRACT)))
    assert r.understand("L001 x") == CONTRACT and len(sent) == 2
    full = json.dumps({"tytul": "W", "strony": [{"tytul": "S", "sekcje": [{"tytul": "A", "pola": [
        {"techName": "a", "tytul": "Pierwsze"}, {"techName": "b", "tytul": "Drugie"}]}]}]})
    r, sent = reader(monkeypatch, answer(full[:full.index("Drugie")], finish_reason="length"))
    data = r.understand("L001 x")
    assert data["_ucieta_odpowiedz"] and len(sent) == 1  # the token limit would cut a retry again
    assert [f["techName"] for f in data["strony"][0]["sekcje"][0]["pola"]] == ["a"]


def test_prompt_names_every_type_validator_and_applicant_datum_of_the_contract():
    """The prompt is prose, so the lists live twice; this keeps them from drifting apart."""
    missing = [name for name in K.TYPES | K.VALIDATORS | K.APPLICANT_DATA if name not in SYSTEM_PROMPT]
    assert missing == []
