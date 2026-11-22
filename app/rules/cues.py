"""Visibility from a paper note such as "(wypełnić tylko w przypadku zaznaczenia kratki w poz. B 1.1.2)".

A note either points at an item ("w poz. 34") or describes an option ("dotyczy wyłącznie X", "w przypadku X").
A note in a heading ("A.", "B.2", capitals) covers the block up to the next heading.
Elsewhere it covers only its own line.
"""

import re

from app.domain.contract import CHOICE_TYPES, leaves
from app.rules.text import Context, norm, sections

HEADING = re.compile(r"^(?:[A-Z]{1,3}|[IVX]+)(?:\.\d+)*[.)]?\s+\S")
STOP = re.compile(r"^(pouczenie|objasnieni|informacja o przetwarzaniu|klauzula)")
TRIGGER = re.compile(
    r"\b(?:wypelnic|wypelnia sie|wypelniamy|dotyczy|stosuje sie)\b"
    r"|\b(?:tylko|wylacznie) (?:w przypadku|gdy|jezeli|dla)\b|^w przypadku\b|\(w przypadku\b"
)
PHRASE = re.compile(
    r"\b(?:dotyczy|stosuje sie|wypelnic|wypelnia sie|wypelniamy)?\s*(?:tylko |wylacznie )*"
    r"(?:w przypadku|gdy|jezeli|dla)\s+(.*)$"
)
POSITION_REF = re.compile(
    r"\bzaznaczeni\w* (?:\w+ )?(?:kratki |pola |opcji )?(?:(tak|nie) )?w poz(?:ycji|\.)? ?([a-z]? ?\d+(?:\.\d+)*)"
)
STEM = 5  # ponytail: a 5-letter prefix instead of a stemmer ("budynek" = "budynku")


def _stems(s):
    return {w[:STEM] for w in re.findall(r"[a-z0-9]+", norm(s)) if len(w) > 3}


def is_heading(text):
    head = text.split("(")[0]
    letters = [ch for ch in head if ch.isalpha()]
    return bool(HEADING.match(text)) and len(letters) > 3 and sum(ch.isupper() for ch in letters) / len(letters) > 0.7


def cue_text(ids, ctx, i):
    """Cue line plus continuation lines while a parenthesis stays open (max 3)."""
    text = ctx.lines[ids[i]]
    for j in range(i + 1, min(i + 4, len(ids))):
        if text.count("(") <= text.count(")"):
            break
        text += " " + ctx.lines[ids[j]]
    return text


def find_cues(ctx):
    """Paper notes as (line id, first and last scope line ids, normalized text), in document order."""
    ids, cues = list(ctx.lines), []
    for i, lid in enumerate(ids):
        raw = ctx.lines[lid]
        if STOP.match(norm(raw)):
            break
        text = norm(cue_text(ids, ctx, i))
        if not TRIGGER.search(text):
            continue
        if is_heading(raw):
            last = i
            while last + 1 < len(ids) and not is_heading(ctx.lines[ids[last + 1]]):
                last += 1
            # continuation lines of an all-caps heading belong to the cue, not to a new heading
            cues.append((lid, ids[i], ids[last], text))
        elif len(raw) < 200:
            cues.append((lid, ids[i], ids[i], text))
    return cues


def _option_line(f, ctx, item):
    return next(
        (ctx.lines[i] for i in f.get("linie") or [] if re.match(rf"^\s*{item}\.\s", ctx.lines.get(i, ""))), None
    )


def resolve_position(c, ctx, ref, yn):
    """('poz. 34', TAK) or ('B 1.1.2') -> (control field, option) or None."""
    nums = re.findall(r"\d+", ref)
    choices = [f for s in sections(c) for f in s["pola"] if f["typ"] in CHOICE_TYPES and f["opcje"]]
    if len(nums) >= 2:  # pozycja.opcja: the item line holds "☐ M. option"
        item, m = nums[-2], int(nums[-1])
        for f in choices:
            line = _option_line(f, ctx, item)
            if line and len(f["opcje"]) >= m and re.search(rf"☐\s*{m}\.", line):
                text = re.split(r"☐", re.split(rf"☐\s*{m}\.", line)[1])[0]
                best = max(f["opcje"], key=lambda o: len(_stems(o) & _stems(text)) / max(len(_stems(o)), 1))
                return f, best if _stems(best) & _stems(text) else f["opcje"][m - 1]
        return None
    item = nums[0]
    for f in choices:
        yes = next((o for o in f["opcje"] if norm(o) == (yn or "tak")), None)
        if yes and _option_line(f, ctx, item) and {norm(o) for o in f["opcje"]} == {"tak", "nie"}:
            return f, yes
    return None


def resolve_phrase(c, phrase):
    """The one option of any choice field whose stems the phrase covers best, or None.
    Needs at least 2 shared stems, a 0.7 share and a 0.2 lead over the runner-up.
    The lead tells 'jednorodzinny' from 'wielolokalowy'."""
    ps, hits = _stems(phrase), []
    for s in sections(c):
        for f in s["pola"]:
            if f["typ"] in CHOICE_TYPES and len(f["opcje"]) >= 2:
                for o in f["opcje"]:
                    so = _stems(o)
                    if len(so & ps) >= 2:
                        hits.append((len(so & ps) / len(so), f, o))
    hits.sort(key=lambda h: -h[0])
    if hits and hits[0][0] >= 0.7 and (len(hits) == 1 or hits[0][0] - hits[1][0] >= 0.2):
        return hits[0][1], hits[0][2]
    return None


def _in_scope(f, order, first, last) -> bool:
    """Every line the field cites lies between the note's first and last line."""
    return bool(f.get("linie")) and all(i in order and order[first] <= order[i] <= order[last] for i in f["linie"])


def _free(cond, names) -> bool:
    """No condition yet, or one that points at a field that does not exist."""
    return not cond or dangling(cond, names)


def dangling(cond, names):
    return any(leaf["pole"] not in names for leaf in leaves(cond))


def condition_from_cue(c: dict, ctx: Context) -> list[str]:
    """Puts a paper note's condition on a section, or on its fields in the note's scope.
    A section gets it only when all its fields are in scope. Model conditions stay unless they point at no field."""
    notes, names = [], {f["techName"] for s in sections(c) for f in s["pola"]}
    order = {lid: n for n, lid in enumerate(ctx.lines)}
    for lid, first, last, text in find_cues(ctx):
        m = POSITION_REF.search(text)
        res = resolve_position(c, ctx, m[2], m[1]) if m else None
        if not res and not m:
            ph = PHRASE.search(text)
            res = resolve_phrase(c, ph[1]) if ph else None
        if not res:
            continue
        ctrl, opt = res
        cond = {"pole": ctrl["techName"], "wartosc": opt}
        for sec in sections(c):
            fs = [f for f in sec["pola"] if f["wzorzec"] == "POLE"]
            if not fs or ctrl in sec["pola"]:
                continue
            if all(_in_scope(f, order, first, last) for f in sec["pola"]) and _free(sec["warunek"], names):
                sec["warunek"] = cond
                notes.append(f"sekcja '{sec['tytul']}' po '{opt}' w '{ctrl['tytul']}' (linia {lid}: {text[:60]})")
                continue
            todo = [
                f
                for f in fs
                if f is not ctrl
                and _in_scope(f, order, first, last)
                and _free(f["warunek"], names)
                and not any(i in ctrl["linie"] for i in f["linie"])
            ]
            if len(todo) >= 2 or (first == last and len(todo) == 1):  # a lone field of a heading block is too ambiguous
                for f in todo:
                    f["warunek"] = cond
                    notes.append(f"pole '{f['tytul']}' po '{opt}' w '{ctrl['tytul']}' (linia {lid}: {text[:60]})")
    return notes
