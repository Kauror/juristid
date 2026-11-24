"""The dates an invitation letter asks for, read deterministically (ADR 0223 §5).

No model. A letter says «Teil on võimalus avaldada eelnõu kohta arvamust ja
esitada ettepanekuid õiguskomisjonile hiljemalt 24.11.26 …» and this reads
24.11.2026 from it, keeps the sentence as evidence, and says what kind of date it
is. What it does not do matters as much:

* **Not every date is a deadline.** «Vabariigi Valitsuse poolt 09.11.26 algatatud»
  is when the bill was initiated. A date counts only in a clause that both asks for
  something (an opinion, proposals, an answer, a word about attending) and marks a
  limit («hiljemalt», «…ks», «…ni», «kuni», «tähtaeg», «ootame»).
* **Different actions keep different dates.** «Palume teatada osalemisest 20.11 ja
  saata arvamus 24.11» is an attendance date and an opinion date. A sitting
  («istung toimub 26.11») is a hearing, not a deadline.
* **Nothing is invented.** A date without a year takes the year from the letter's
  own date only when that gives a day 0–120 days after the letter; otherwise it
  needs checking. A weekday («reedeks») is never turned into a date. A time is kept
  only when written («kell 12.00»), in Europe/Tallinn as the letter means it.
* **Doubt is said, not hidden.** Two different opinion dates without an extension,
  a date before the letter, an unparseable day: the result is «Tähtaeg vajab
  kontrollimist» with the reason, and the sentence is still shown.

`extract` is pure: the same text and letter date give the same answer.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from app.riigikogu.enums import DeadlineKind, ExtractionStatus

#: Month stems as they appear in any case form («novembriks», «novembrini», «märtsil»).
MONTHS = (
    ("jaanuar", 1),
    ("veebruar", 2),
    ("märts", 3),
    ("aprill", 4),
    ("mai", 5),
    ("juuni", 6),
    ("juuli", 7),
    ("august", 8),
    ("septemb", 9),
    ("oktoob", 10),
    ("novemb", 11),
    ("detsemb", 12),
)
_MONTH_ALT = "|".join(stem for stem, _ in MONTHS)

#: 24.11.2026 · 24.11.26 · 24.11. · 24.11 · 24/11/2026
_NUMERIC = re.compile(
    r"(?<![\d.])(\d{1,2})\s?[./]\s?(\d{1,2})(?:\s?[./]\s?(\d{4}|\d{2}))?\.?(?![\d])"
)
#: 24. novembriks · 24 novembrini 2026 · 24. novembril 2026. a
_WRITTEN = re.compile(rf"(?<!\d)(\d{{1,2}})\.?\s+({_MONTH_ALT})\w*(?:\s+(\d{{4}}))?", re.IGNORECASE)
#: kell 12.00 · kell 12:00 · kl 12 · 12.00-ks (only after «kell»/«kl»: a bare 12.00 is a date)
_TIME = re.compile(r"\b(?:kell|kl\.?)\s*(\d{1,2})(?:[.:](\d{2}))?", re.IGNORECASE)
#: A weekday used as a deadline («reedeks», «esmaspäevaks»): never made into a date.
_WEEKDAY = re.compile(
    r"\b(esmaspäev|teisipäev|kolmapäev|neljapäev|reede|laupäev|pühapäev)\w*\b", re.IGNORECASE
)

_OPINION = re.compile(
    r"arvamus|arvamust|ettepanek|seisukoh|tagasisid|kommentaar|märkus|vastata|vastus|"
    r"kirjalikult",
    re.IGNORECASE,
)
_ATTENDANCE = re.compile(
    r"osalemis|osaleda|osalejat|registreeru|osavõt|kas\s+soovite\s+osaleda|esindaja",
    re.IGNORECASE,
)
_HEARING = re.compile(
    r"istung|arutelu\s+toimu|toimub|kohtumi|ümarlaud|arutatakse|aruteluks\s+kokku",
    re.IGNORECASE,
)
#: A limit: by, until, no later than, the deadline is, we expect … by.
_LIMIT = re.compile(
    r"hiljemalt|tähtaeg|tähtajaga|\bkuni\b|ootame|palume|esitada|saata|teatada|"
    rf"\d\s*({_MONTH_ALT})\w*(ks|ni)\b|\d\.?\s*(ks|ni)\b|-ks\b|-ni\b",
    re.IGNORECASE,
)
_EXTENSION = re.compile(
    r"pikend|uus\s+tähtaeg|muudame\s+tähtaega|tähtaeg\s+on\s+nüüd", re.IGNORECASE
)
_CANCELLED = re.compile(
    r"tühista|jääb\s+ära|ära\s+jäetud|ei\s+toimu|edasi\s+lükatud|palume\s+(seda\s+)?mitte\s+arvestada",
    re.IGNORECASE,
)
#: Clauses within a sentence: «…, istung toimub 26.11», «… ja saata arvamus 24.11».
_CLAUSE = re.compile(r"\s*(?:;|,|\s+ning\s+|\s+ja\s+(?=\w+\s))\s*")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÕÄÖÜŠŽ«„])|\n+")

#: A deadline more than this long after the letter is doubtful (a year-typo, a reused letter).
MAX_AHEAD_DAYS = 120


@dataclass(frozen=True)
class FoundDate:
    kind: str
    day: dt.date | None
    time: dt.time | None
    status: str
    evidence: str
    doubt: str = ""
    extension: bool = False


@dataclass(frozen=True)
class Extraction:
    #: The deadlines, at most one per kind (the chosen one); `status` is the opinion date's.
    dates: tuple[FoundDate, ...]
    status: str
    hearing_on: dt.date | None = None
    hearing_evidence: str = ""
    cancelled: bool = False
    #: Everything considered, for the evaluation report.
    candidates: tuple[FoundDate, ...] = field(default=())

    def date_of(self, kind: str) -> FoundDate | None:
        return next((d for d in self.dates if d.kind == kind), None)


@dataclass(frozen=True)
class _Mention:
    day: int
    month: int
    year: int | None
    start: int
    end: int


def _mentions(clause: str) -> list[_Mention]:
    found: list[_Mention] = []
    for match in _WRITTEN.finditer(clause):
        stem = match.group(2).casefold()
        month = next(number for s, number in MONTHS if stem.startswith(s))
        year = int(match.group(3)) if match.group(3) else None
        found.append(_Mention(int(match.group(1)), month, year, match.start(), match.end()))
    taken = [(m.start, m.end) for m in found]
    for match in _NUMERIC.finditer(clause):
        if any(a <= match.start() < b for a, b in taken):
            continue
        # «kell 12.00» is a time, not the twelfth of no month.
        before = clause[max(0, match.start() - 6) : match.start()].casefold()
        if re.search(r"(kell|kl\.?)\s*$", before):
            continue
        day, month = int(match.group(1)), int(match.group(2))
        if not (1 <= month <= 12 and 1 <= day <= 31):
            continue
        raw_year = match.group(3)
        year = None
        if raw_year:
            year = int(raw_year) + (2000 if len(raw_year) == 2 else 0)
        found.append(_Mention(day, month, year, match.start(), match.end()))
    return sorted(found, key=lambda m: m.start)


def _time_in(clause: str) -> dt.time | None:
    match = _TIME.search(clause)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    if hour > 23 or minute > 59:
        return None
    return dt.time(hour, minute)


def _resolve(mention: _Mention, letter_date: dt.date | None) -> tuple[dt.date | None, str]:
    """The calendar day, or ``None`` and why not."""
    if mention.year is not None:
        try:
            return dt.date(mention.year, mention.month, mention.day), ""
        except ValueError:
            return None, "kuupäev ei ole kalendris olemas"
    if letter_date is None:
        return None, "aasta puudub ja kirja kuupäev pole teada"
    for year in (letter_date.year, letter_date.year + 1):
        try:
            day = dt.date(year, mention.month, mention.day)
        except ValueError:
            continue
        if 0 <= (day - letter_date).days <= MAX_AHEAD_DAYS:
            return day, ""
    return None, "aasta puudub ja kirja kuupäevast ei selgu, mis aastat mõeldakse"


def _kind_of(clause: str, sentence: str) -> str | None:
    """The action the clause asks a date for; the sentence's when the clause names none."""
    if _HEARING.search(clause) and not _LIMIT.search(clause):
        return "HEARING"
    if _ATTENDANCE.search(clause):
        return DeadlineKind.ATTENDANCE
    if _OPINION.search(clause):
        return DeadlineKind.OPINION_REQUEST
    if _HEARING.search(clause):
        return "HEARING"
    # «… ja esitada hiljemalt 24.11»: the request word is in another clause of the sentence.
    if _ATTENDANCE.search(sentence) and not _OPINION.search(sentence):
        return DeadlineKind.ATTENDANCE
    if _OPINION.search(sentence):
        return DeadlineKind.OPINION_REQUEST
    # «Pikendame varasemat tähtaega 28. novembrini»: in an invitation the deadline
    # that is extended is the one the letter asked for — the opinion's.
    if _EXTENSION.search(sentence) and re.search(r"tähtaeg", sentence, re.IGNORECASE):
        return DeadlineKind.OPINION_REQUEST
    return None


def _sentences(text: str) -> list[str]:
    flat = re.sub(r"[ \t\r\f\v]+", " ", text or "")
    return [s.strip() for s in _SENTENCE.split(flat) if s and s.strip()]


def extract(text: str, *, letter_date: dt.date | None) -> Extraction:
    """Read the deadlines of one invitation letter."""
    if not (text or "").strip():
        return Extraction(dates=(), status=ExtractionStatus.UNREADABLE)
    candidates: list[FoundDate] = []
    hearing: tuple[dt.date, str] | None = None
    cancelled = False
    for sentence in _sentences(text):
        if _CANCELLED.search(sentence) and re.search(
            r"kutse|kaasami|arvamus|istung|arutelu", sentence, re.IGNORECASE
        ):
            cancelled = cancelled or not _EXTENSION.search(sentence)
        extension = bool(_EXTENSION.search(sentence))
        for clause in _CLAUSE.split(sentence):
            mentions = _mentions(clause)
            weekday = _WEEKDAY.search(clause)
            if not mentions and weekday and _LIMIT.search(clause):
                kind = _kind_of(clause, sentence)
                if kind in (DeadlineKind.OPINION_REQUEST, DeadlineKind.ATTENDANCE):
                    candidates.append(
                        FoundDate(
                            kind=kind,
                            day=None,
                            time=None,
                            status=ExtractionStatus.CHECK,
                            evidence=sentence,
                            doubt=f"tähtajaks on nädalapäev («{weekday.group(0)}»), mitte kuupäev",
                        )
                    )
                continue
            if not mentions:
                continue
            kind = _kind_of(clause, sentence)
            if kind is None:
                continue
            for mention in mentions:
                day, why = _resolve(mention, letter_date)
                if kind == "HEARING":
                    if day is not None and hearing is None:
                        hearing = (day, sentence)
                    continue
                if not _LIMIT.search(clause) and not _LIMIT.search(sentence):
                    continue
                status = ExtractionStatus.FOUND if day is not None else ExtractionStatus.CHECK
                doubt = why
                if day is not None and letter_date is not None and day < letter_date:
                    status, doubt = ExtractionStatus.CHECK, "tähtaeg on varasem kui kirja kuupäev"
                if (
                    day is not None
                    and letter_date is not None
                    and (day - letter_date).days > MAX_AHEAD_DAYS
                ):
                    status, doubt = (
                        ExtractionStatus.CHECK,
                        f"tähtaeg on üle {MAX_AHEAD_DAYS} päeva kirjast hiljem",
                    )
                candidates.append(
                    FoundDate(
                        kind=kind,
                        day=day,
                        time=_time_in(clause),
                        status=status,
                        evidence=sentence,
                        doubt=doubt,
                        extension=extension,
                    )
                )
    chosen = tuple(
        found for found in (_choose(candidates, kind) for kind in DeadlineKind) if found is not None
    )
    opinion = next((d for d in chosen if d.kind == DeadlineKind.OPINION_REQUEST), None)
    if opinion is not None:
        status = opinion.status
    elif chosen:
        status = (
            ExtractionStatus.CHECK
            if any(d.status != ExtractionStatus.FOUND for d in chosen)
            else ExtractionStatus.FOUND
        )
    else:
        status = ExtractionStatus.NOT_FOUND
    return Extraction(
        dates=chosen,
        status=status,
        hearing_on=hearing[0] if hearing else None,
        hearing_evidence=hearing[1] if hearing else "",
        cancelled=cancelled,
        candidates=tuple(candidates),
    )


def _choose(candidates: list[FoundDate], kind: str) -> FoundDate | None:
    """One date of a kind: the extension's if the letter moves a date, else the only one.

    Two different days of one kind and no extension between them is doubt, said.
    """
    mine = [c for c in candidates if c.kind == kind]
    if not mine:
        return None
    extended = [c for c in mine if c.extension and c.day is not None]
    if extended:
        latest = max(extended, key=lambda c: c.day or dt.date.min)
        return latest
    days = {c.day for c in mine if c.day is not None}
    if len(days) > 1:
        first = mine[0]
        listed = ", ".join(sorted(d.strftime("%d.%m.%Y") for d in days))
        return FoundDate(
            kind=kind,
            day=first.day,
            time=first.time,
            status=ExtractionStatus.CHECK,
            evidence=first.evidence,
            doubt=f"kirjas on mitu kuupäeva ({listed})",
        )
    found = [c for c in mine if c.day is not None]
    if found:
        best = found[0]
        # A time given in another clause of the same sentence still belongs to it.
        if best.time is None:
            best = next((c for c in found if c.time is not None), best)
        return best
    return mine[0]


def format_due(day: dt.date | None, time: dt.time | None) -> str:
    if day is None:
        return ""
    text = day.strftime("%d.%m.%Y")
    return f"{text} kell {time.strftime('%H.%M')}" if time else text
