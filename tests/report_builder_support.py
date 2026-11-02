"""Shared synthetic records and form handling for the report-builder tests.

Every record is built the way the product stores it — a SENT Submission with
its final evidence, an Entry with a Tallinn timestamp, a register row with
both derivations of `VÄLJA` — so the source rules are tested against the
shapes they will meet. Every value is synthetic.

`form_fields` reads the composer's form back out of a rendered response, so a
"round trip" in these tests is the page's own fields posted again, not a dict
a test wrote by hand.
"""

from __future__ import annotations

import html
import re
from collections import defaultdict
from datetime import date, datetime, time
from html.parser import HTMLParser
from typing import Any

from django.utils import timezone

from app.core.enums import Visibility
from app.legacy_import.current_state import CurrentRegisterState, RegisterCurrency
from app.legacy_import.register_semantics import AddresseeCardinality, OpinionSentState
from app.matters.entry_enums import EntryKind
from app.matters.enums import WebsiteOverviewKind, WebsiteOverviewStatus
from app.matters.models import Entry, MatterWebsiteOverview
from app.submissions.enums import RecipientRole, SentAtPrecision, SubmissionKind, SubmissionStatus
from app.submissions.models import Submission, SubmissionRecipient
from tests import factories

OCTOBER = (date(2026, 10, 1), date(2026, 10, 31))


def at(day: date, hour: int = 12) -> datetime:
    """A Tallinn wall-clock moment on ``day``."""
    return timezone.make_aware(datetime.combine(day, time(hour)))


def sent(
    matter: Any,
    capture_evidence: Any,
    *,
    day: date,
    kind: str = SubmissionKind.FORMAL_OPINION,
    status: str = SubmissionStatus.SENT,
    summary: str = "",
    title: str = "Koja arvamus",
    restricted: bool = False,
    addressees: tuple[Any, ...] = (),
    copies: tuple[Any, ...] = (),
) -> Submission:
    """A send on ``day``, stored as Tallinn midnight at DATE precision."""
    version = capture_evidence(
        matter,
        f"%PDF-1.4 {matter.pk} {day} {title}".encode(),
        "arvamus.pdf",
        "application/pdf",
        visibility_override=Visibility.RESTRICTED if restricted else "",
    )
    submission = Submission.objects.create(
        matter=matter,
        title=title,
        kind=kind,
        status=status,
        sent_at=timezone.make_aware(datetime.combine(day, time.min)),
        sent_at_precision=SentAtPrecision.DATE,
        final_version=version,
        summary=summary,
        visibility_override=Visibility.RESTRICTED if restricted else "",
    )
    for organisation in addressees:
        SubmissionRecipient.objects.create(
            submission=submission, organisation=organisation, role=RecipientRole.ADDRESSEE
        )
    for organisation in copies:
        SubmissionRecipient.objects.create(
            submission=submission, organisation=organisation, role=RecipientRole.FOR_INFORMATION
        )
    return submission


def entry(
    matter: Any,
    *,
    day: date,
    kind: str = EntryKind.MEETING,
    body: str = "<p>Kohtumine ministeeriumiga.</p>",
    organisation: Any = None,
    restricted: bool = False,
    hour: int = 10,
) -> Entry:
    return factories.EntryFactory(
        matter=matter,
        kind=kind,
        occurred_at=at(day, hour),
        body=body,
        organisation=organisation,
        visibility_override=Visibility.RESTRICTED if restricted else "",
    )


def overview(
    matter: Any,
    *,
    day: date | None,
    kind: str = WebsiteOverviewKind.OVERVIEW,
    title: str = "Koja ülevaade",
    url: str = "https://www.koda.ee/et/hetkel-kasil/naidis",
) -> MatterWebsiteOverview:
    return MatterWebsiteOverview.objects.create(
        matter=matter,
        status=WebsiteOverviewStatus.PUBLISHED,
        kind=kind,
        url=url,
        title=title,
        published_on=day,
        published_at=timezone.now(),
    )


def register(
    matter: Any,
    *,
    day: date | None,
    state: str = OpinionSentState.DATE,
    sheet: str = "2026",
    row: int = 14,
    addressee_raw: str = "",
    cardinality: str = AddresseeCardinality.BLANK,
) -> CurrentRegisterState:
    reference = factories.MatterSourceReferenceFactory(
        matter=matter, source_sheet=sheet, source_row_number=row
    )
    return CurrentRegisterState.objects.create(
        matter=matter,
        source_reference=reference,
        source_snapshot_sha256="1" * 64,
        source_sheet=sheet,
        source_row_number=row,
        currency=RegisterCurrency.CURRENT,
        opinion_sent_recorded=state != OpinionSentState.BLANK,
        opinion_sent_state=state,
        opinion_sent_date=day if state == OpinionSentState.DATE else None,
        addressee_raw=addressee_raw,
        addressee_cardinality=cardinality,
        observed_at=timezone.now(),
    )


# ---------------------------------------------------------------------------
# The page's own form, read back
# ---------------------------------------------------------------------------


class _Form(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fields: list[tuple[str, str]] = []
        self._textarea: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: (value or "") for key, value in attrs}
        name = values.get("name")
        if tag == "textarea" and name:
            self._textarea, self._text = name, []
            return
        if tag != "input" or not name:
            return
        kind = values.get("type", "text")
        if kind in ("submit", "button"):
            return
        if kind == "checkbox" and "checked" not in values:
            return
        self.fields.append((name, values.get("value", "")))

    def handle_data(self, data: str) -> None:
        if self._textarea is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "textarea" and self._textarea is not None:
            text = "".join(self._text)
            # The browser drops one newline straight after <textarea>.
            self.fields.append((self._textarea, text[1:] if text.startswith("\n") else text))
            self._textarea = None


def form_fields(response: Any) -> list[tuple[str, str]]:
    """The composer's fields as the browser would post them, in page order."""
    content = response.content.decode()
    match = re.search(r'id="report-body".*', content, re.S)
    body = match.group(0) if match else content
    parser = _Form()
    parser.feed(body)
    parser.close()
    return [(name, value) for name, value in parser.fields if name != "csrfmiddlewaretoken"]


def as_post(fields: list[tuple[str, str]], **extra: str) -> dict[str, list[str]]:
    data: dict[str, list[str]] = defaultdict(list)
    for name, value in fields:
        data[name].append(value)
    for name, value in extra.items():
        data[name] = [value]
    return dict(data)


def values(fields: list[tuple[str, str]], name: str) -> list[str]:
    return [value for key, value in fields if key == name]


def replace(fields: list[tuple[str, str]], name: str, value: str) -> list[tuple[str, str]]:
    return [(key, value if key == name else old) for key, old in fields]


def preview_rows(response: Any) -> list[list[str]]:
    """The preview table's rows as cell texts, a spanned topic cell repeated."""
    content = response.content.decode()
    table = re.search(r"<table[^>]*data-report-preview.*?</table>", content, re.S)
    if table is None:
        return []
    rows = []
    topic = ""
    for row in re.findall(r"<tr>(.*?)</tr>", table.group(0), re.S)[1:]:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(cells) == 4:
            topic = _text(cells[0])
            cells = cells[1:]
        rows.append([topic, *(_text(cell) for cell in cells)])
    return rows


def _text(fragment: str) -> str:
    fragment = re.sub(r"<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"<span[^>]*>", "\n", fragment)
    fragment = re.sub(r"<[^>]+>", "", fragment)
    return "\n".join(line.strip() for line in html.unescape(fragment).split("\n") if line.strip())
