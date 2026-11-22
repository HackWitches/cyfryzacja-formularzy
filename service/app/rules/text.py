"""Shared helpers: text normalization, the form lines, and walking the contract's fields."""
import re
import unicodedata
from dataclasses import dataclass, field

LINE = re.compile(r"^(L\d+) (.*)$")


@dataclass
class Context:
    text: str  # numbered lines exactly as sent to the model
    lines: dict = field(default_factory=dict)  # line id -> text

    @classmethod
    def of(cls, lines_text: str) -> "Context":
        return cls(lines_text, {m[1]: m[2] for m in map(LINE.match, lines_text.splitlines()) if m})


def norm(s) -> str:
    """Lower case, no Polish diacritics, single spaces: what every rule expression is written against."""
    s = unicodedata.normalize("NFKD", str(s or "").replace("ł", "l").replace("Ł", "L"))
    return " ".join(s.encode("ascii", "ignore").decode().lower().split())


def field_lines(f: dict, ctx: Context) -> list[str]:
    return [ctx.lines[i] for i in f.get("linie") or [] if i in ctx.lines]


def sections(c: dict):
    for page in c["strony"]:
        yield from page["sekcje"]


def plain_fields(c: dict):
    """Fields the model typed itself (macros ADRES/OSOBA are expanded by the builder with fixed types)."""
    for sec in sections(c):
        for f in sec["pola"]:
            if f["wzorzec"] == "POLE":
                yield sec, f
