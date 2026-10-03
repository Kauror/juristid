"""The third consistency batch, 30 September 2026 (docs/adr/0122).

`tests/test_user_correction_batch_two.py` holds docs/adr/0121 and still runs
unchanged beside this module. This one holds what docs/adr/0122 made consistent:

1. **one e-mail is one `Teema käik` addition**, however many attachments it
   carried — the rule an upload of several files already followed;
2. **a broad period is classified as the period it is** on every deadline
   surface — Minu asjad, the register's deadline populations, Ülevaade and
   Osakond — through one rule, and becomes past only when it has ended;
3. **no raw control byte in production source** — the `\\b` in
   `opinion_sources.ADDRESSEE_SEPARATOR` that had been saved as a backspace.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.audit.operations import composer_operation, current_operation_id, separate_operation
from app.core.enums import Visibility
from app.documents.derivatives import EmailAttachmentLink
from app.documents.email_intake import attachments_of, email_intake_operation_id, parent_email_of
from app.documents.enums import DocumentRole
from app.documents.models import Document, DocumentVersion
from app.intelligence.services import add_important_date
from app.matters import work_items as wi
from app.matters.department_dashboard import upcoming_groups
from app.matters.models import Matter
from app.matters.my_work import HORIZON_ALL, build_my_work, horizon_from
from app.matters.register_filters import register_population
from app.matters.timeline import documents_added_clause, matter_timeline
from app.workflow.dates import period_bounds, period_in_window
from app.workflow.enums import DatePrecision
from app.workflow.models import NextAction
from app.workflow.services import set_next_action
from tests import factories
from tests import synthetic_corpus as corpus

pytestmark = pytest.mark.django_db

REPO = Path(__file__).resolve().parent.parent
EML = "message/rfc822"
PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


# ---------------------------------------------------------------------------
# 1 — one e-mail, one Teema käik addition
# ---------------------------------------------------------------------------


def _pdf(label: str) -> bytes:
    return corpus.text_pdf([f"Lisa {label}"])


def _eml(subject: str, *parts: tuple[str, bytes], inline_logo: bool = False) -> bytes:
    """A received e-mail carrying exactly ``parts`` as attachments."""
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = "keegi@naidisministeerium.invalid"
    message["To"] = "oigus@koda.invalid"
    message.set_content(f"{subject}. Manused on lisatud.")
    for filename, payload in parts:
        # The evidence store decides a type by extension, never by what the
        # message declares, so every part is declared as bytes of a PDF.
        message.add_attachment(payload, maintype="application", subtype="pdf", filename=filename)
    if inline_logo:
        message.add_attachment(
            b"\x89PNG\r\n\x1a\n" + b"0" * 32,
            maintype="image",
            subtype="png",
            disposition="inline",
            cid="<logo@naidis.invalid>",
        )
    return message.as_bytes()


def _pdfs(count: int, prefix: str = "lisa") -> list[tuple[str, bytes]]:
    return [(f"{prefix}-{n}.pdf", _pdf(f"{prefix} {n}")) for n in range(1, count + 1)]


def _receive(capture_evidence, matter, actor, message: bytes, name: str = "kiri.eml"):
    """The message filed on the Matter, the way a person files an e-mail."""
    return capture_evidence(matter, message, name, EML, uploaded_by=actor, created_by=actor)


def _upload_rows(matter, user):
    items, _more = matter_timeline(matter=matter, user=user, limit=200)
    return [
        item
        for item in items
        if any(event.event_type == ChangeEventType.EVIDENCE_VERSION_ADDED for event in item.events)
    ]


def _intake_rows(matter, user, message_version):
    """The rows of one e-mail's attachments — its operation, and nothing else."""
    operation = email_intake_operation_id(message_version)
    return [
        row
        for row in _upload_rows(matter, user)
        if any(event.operation_id == operation for event in row.events)
    ]


@pytest.mark.parametrize("count", [1, 2, 3, 8, 13])
def test_one_email_is_one_row_that_counts_its_attachments(
    normal_matter, specialist, capture_evidence, extract, count
):
    message = _receive(capture_evidence, normal_matter, specialist, _eml("Kiri", *_pdfs(count)))

    report = extract(message)

    assert report.state == "DONE"
    assert report.attachments == count
    rows = _intake_rows(normal_matter, specialist, message)
    assert len(rows) == 1
    assert rows[0].summary_sentence == documents_added_clause(count)
    assert len(rows[0].files) == count
    assert rows[0].actor == specialist
    # The message's own upload is its own row, and the only other one.
    assert [row.summary_sentence for row in _upload_rows(normal_matter, specialist)] == [
        documents_added_clause(count),
        "lisas dokumendi",
    ]


def test_one_attachment_reads_in_the_singular(normal_matter, specialist, capture_evidence, extract):
    message = _receive(capture_evidence, normal_matter, specialist, _eml("Üks", *_pdfs(1)))

    extract(message)

    assert [row.summary_sentence for row in _intake_rows(normal_matter, specialist, message)] == [
        "lisas dokumendi"
    ]


def test_every_attachment_keeps_its_own_records(
    normal_matter, specialist, capture_evidence, extract
):
    """The grouping is a reading. Every document, version, link row and audit
    event is still there, one per attachment, pointing at its message."""
    message = _receive(capture_evidence, normal_matter, specialist, _eml("Neli", *_pdfs(4)))

    extract(message)

    attachments = Document.objects.filter(matter=normal_matter, role=DocumentRole.EMAIL_ATTACHMENT)
    assert attachments.count() == 4
    versions = DocumentVersion.objects.filter(document__in=attachments)
    assert versions.count() == 4
    assert len({version.sha256 for version in versions}) == 4
    links = list(attachments_of(message, viewer=specialist))
    assert [link.ordinal for link in links] == [1, 2, 3, 4]
    assert all(
        parent_email_of(v, viewer=specialist).parent_version_id == message.pk for v in versions
    )
    operation = email_intake_operation_id(message)
    for event_type in (ChangeEventType.DOCUMENT_CREATED, ChangeEventType.EVIDENCE_VERSION_ADDED):
        events = ChangeEvent.objects.filter(matter=normal_matter, event_type=event_type).exclude(
            object_id__in=[message.document_id, message.pk]
        )
        assert events.count() == 4, event_type
        assert {event.operation_id for event in events} == {operation}
        assert {event.actor_id for event in events} == {specialist.pk}


def test_two_emails_are_two_rows(normal_matter, specialist, capture_evidence, extract):
    first = _receive(
        capture_evidence, normal_matter, specialist, _eml("A", *_pdfs(6, "a")), "a.eml"
    )
    second = _receive(
        capture_evidence, normal_matter, specialist, _eml("B", *_pdfs(3, "b")), "b.eml"
    )

    extract(first)
    extract(second)

    assert email_intake_operation_id(first) != email_intake_operation_id(second)
    assert [r.summary_sentence for r in _intake_rows(normal_matter, specialist, first)] == [
        "lisas 6 dokumenti"
    ]
    assert [r.summary_sentence for r in _intake_rows(normal_matter, specialist, second)] == [
        "lisas 3 dokumenti"
    ]
    assert sorted(r.summary_sentence for r in _upload_rows(normal_matter, specialist)) == [
        "lisas 3 dokumenti",
        "lisas 6 dokumenti",
        "lisas dokumendi",
        "lisas dokumendi",
    ]


def test_two_emails_read_in_the_same_instant_stay_two_rows(
    normal_matter, specialist, capture_evidence, extract, monkeypatch
):
    """Grouping is by the e-mail, never by the clock."""
    first = _receive(
        capture_evidence, normal_matter, specialist, _eml("A", *_pdfs(2, "a")), "a.eml"
    )
    second = _receive(
        capture_evidence, normal_matter, specialist, _eml("B", *_pdfs(2, "b")), "b.eml"
    )
    instant = timezone.now().replace(microsecond=0)
    field = ChangeEvent._meta.get_field("occurred_at")
    monkeypatch.setattr(field, "_get_default", lambda: instant)

    extract(first)
    extract(second)

    added = ChangeEvent.objects.filter(
        matter=normal_matter,
        event_type=ChangeEventType.EVIDENCE_VERSION_ADDED,
        occurred_at=instant,
    )
    assert added.count() == 4
    assert len(_intake_rows(normal_matter, specialist, first)) == 1
    assert len(_intake_rows(normal_matter, specialist, second)) == 1
    assert [
        row.summary_sentence
        for row in _upload_rows(normal_matter, specialist)
        if row.summary_sentence != "lisas dokumendi"
    ] == ["lisas 2 dokumenti", "lisas 2 dokumenti"]


def test_refused_attachments_are_not_counted(normal_matter, specialist, capture_evidence, extract):
    """Only what was added is counted: an empty part, a type the evidence store
    refuses and an inline logo add nothing and say nothing."""
    message = _eml(
        "Segamini",
        ("lisa-1.pdf", _pdf("1")),
        ("tuhi.pdf", b""),
        ("programm.exe", b"MZ" + b"0" * 64),
        ("lisa-2.pdf", _pdf("2")),
        inline_logo=True,
    )
    version = _receive(capture_evidence, normal_matter, specialist, message)

    report = extract(version)

    assert report.state == "DONE"
    assert report.attachments == 2
    rows = _intake_rows(normal_matter, specialist, version)
    assert [row.summary_sentence for row in rows] == ["lisas 2 dokumenti"]
    assert sorted(file.label for file in rows[0].files) == ["lisa-1.pdf", "lisa-2.pdf"]


def test_an_email_with_nothing_usable_adds_no_row(
    normal_matter, specialist, capture_evidence, extract
):
    message = _eml("Tühi", ("tuhi.pdf", b""), ("programm.exe", b"MZ0000"), inline_logo=True)
    version = _receive(capture_evidence, normal_matter, specialist, message)

    report = extract(version)

    assert report.state == "DONE"
    assert report.attachments == 0
    assert _intake_rows(normal_matter, specialist, version) == []
    assert [row.summary_sentence for row in _upload_rows(normal_matter, specialist)] == [
        "lisas dokumendi"
    ]
    assert not ChangeEvent.objects.filter(operation_id=email_intake_operation_id(version)).exists()


def test_a_forced_re_read_adds_nothing_and_keeps_one_row(
    normal_matter, specialist, capture_evidence, extract
):
    version = _receive(capture_evidence, normal_matter, specialist, _eml("Kaks", *_pdfs(2)))

    extract(version)
    extract(version, force=True)

    assert Document.objects.filter(role=DocumentRole.EMAIL_ATTACHMENT).count() == 2
    assert [r.summary_sentence for r in _intake_rows(normal_matter, specialist, version)] == [
        "lisas 2 dokumenti"
    ]


def test_a_later_pass_over_the_same_email_joins_its_row(
    normal_matter, specialist, capture_evidence, extract, settings
):
    """The identifier is the message's, so an attachment a first pass had to
    skip and a second pass adds belongs to the same e-mail's row."""
    small, large = _pdf("väike"), _pdf("suur") + b"%" * 4096
    version = _receive(
        capture_evidence,
        normal_matter,
        specialist,
        _eml("Hiljem", ("vaike.pdf", small), ("suur.pdf", large)),
    )
    limit = settings.MAX_EVIDENCE_UPLOAD_BYTES
    settings.MAX_EVIDENCE_UPLOAD_BYTES = len(small) + 1
    extract(version)
    assert [r.summary_sentence for r in _intake_rows(normal_matter, specialist, version)] == [
        "lisas dokumendi"
    ]

    settings.MAX_EVIDENCE_UPLOAD_BYTES = limit
    extract(version, force=True)

    assert [r.summary_sentence for r in _intake_rows(normal_matter, specialist, version)] == [
        "lisas 2 dokumenti"
    ]


def test_a_message_inside_a_message_is_an_email_of_its_own(
    normal_matter, specialist, capture_evidence, extract
):
    inner = _eml("Edastatud", *_pdfs(2, "sisemine"))
    outer = _receive(
        capture_evidence,
        normal_matter,
        specialist,
        _eml("Edasi", ("edastatud.eml", inner), ("kate.pdf", _pdf("kate"))),
    )

    extract(outer)
    nested = DocumentVersion.objects.get(original_filename="edastatud.eml")
    extract(nested)

    assert [r.summary_sentence for r in _intake_rows(normal_matter, specialist, outer)] == [
        "lisas 2 dokumenti"
    ]
    assert [r.summary_sentence for r in _intake_rows(normal_matter, specialist, nested)] == [
        "lisas 2 dokumenti"
    ]


def test_an_operation_around_the_read_never_absorbs_the_email(
    normal_matter, specialist, capture_evidence, extract
):
    """Two messages opened inside one outer operation are still two e-mails."""
    first = _receive(
        capture_evidence, normal_matter, specialist, _eml("A", *_pdfs(2, "a")), "a.eml"
    )
    second = _receive(
        capture_evidence, normal_matter, specialist, _eml("B", *_pdfs(3, "b")), "b.eml"
    )

    with composer_operation() as outer:
        extract(first)
        extract(second)
        assert current_operation_id() == outer

    assert [r.summary_sentence for r in _intake_rows(normal_matter, specialist, first)] == [
        "lisas 2 dokumenti"
    ]
    assert [r.summary_sentence for r in _intake_rows(normal_matter, specialist, second)] == [
        "lisas 3 dokumenti"
    ]
    assert not ChangeEvent.objects.filter(operation_id=outer).exists()


def test_a_separate_operation_restores_the_one_around_it():
    own = uuid.uuid4()
    with composer_operation() as outer:
        with separate_operation(own):
            assert current_operation_id() == own
            with composer_operation() as inner:
                # A service called inside still joins the separate one.
                assert inner == own
        assert current_operation_id() == outer
    assert current_operation_id() is None


def test_the_email_operation_is_the_messages_own(normal_matter, capture_evidence):
    """Stable for one message, different for two — computed, never stored."""
    a = capture_evidence(normal_matter, _eml("A", *_pdfs(1)), "a.eml", EML)
    b = capture_evidence(normal_matter, _eml("B", *_pdfs(1)), "b.eml", EML)

    assert email_intake_operation_id(a) == email_intake_operation_id(a)
    assert email_intake_operation_id(a) != email_intake_operation_id(b)


def test_the_teema_page_prints_one_line_for_the_email(
    signed_in, normal_matter, specialist, capture_evidence, extract
):
    message = _receive(capture_evidence, normal_matter, specialist, _eml("Kaheksa", *_pdfs(8)))
    extract(message)

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk})
    ).content.decode()

    assert body.count("lisas 8 dokumenti") == 1
    # The one «lisas dokumendi» on the page is the message itself.
    assert body.count("lisas dokumendi") == 1
    for n in range(1, 9):
        assert f"lisa-{n}.pdf" in body


def test_uploads_and_emails_share_one_grouping(signed_in, specialist, capture_evidence, extract):
    """`Saabunud` with an e-mail and two files is one upload row; the e-mail's
    own attachments are the next one. Both read through `operation_id`."""
    files = [
        ("kiri.eml", _eml("Saabunud kiri", *_pdfs(2, "manus")), EML),
        ("a.pdf", PDF + b"a", "application/pdf"),
        ("b.pdf", PDF + b"b", "application/pdf"),
    ]

    signed_in.post(
        reverse("matters:intake"),
        {
            "title": "Saabunud e-kiri",
            "visibility": Visibility.NORMAL,
            "uploads": [SimpleUploadedFile(n, c, content_type=t) for n, c, t in files],
        },
    )
    matter = Matter.objects.get(title="Saabunud e-kiri")
    assert [row.summary_sentence for row in _upload_rows(matter, specialist)] == [
        "lisas 3 dokumenti"
    ]

    message = DocumentVersion.objects.get(document__matter=matter, original_filename="kiri.eml")
    extract(message)

    rows = _upload_rows(matter, specialist)
    assert [row.summary_sentence for row in rows] == ["lisas 2 dokumenti", "lisas 3 dokumenti"]
    upload_ops = {e.operation_id for e in rows[1].events}
    intake_ops = {e.operation_id for e in rows[0].events}
    assert len(upload_ops) == len(intake_ops) == 1
    assert upload_ops != intake_ops
    assert intake_ops == {email_intake_operation_id(message)}
    assert EmailAttachmentLink.objects.filter(parent_version=message).count() == 2


# ---------------------------------------------------------------------------
# 2 — a broad period is classified as the period it is, on every surface
# ---------------------------------------------------------------------------

#: A Wednesday. October begins tomorrow — the anchor every old window read
#: «oktoober 2026» as — and this ISO week runs to Sunday 4 October.
TODAY = date(2026, 9, 30)

#: Where one record lands, read the way each surface reads it: the Minu asjad
#: band, the register's deadline populations, the Osakond window, the Ülevaade
#: strip's «tähtaeg 30 p jooksul», «Tähtaeg ees», and whether it is overdue.
Placement = tuple[str | None, frozenset[str], str | None, bool, bool, bool]

REGISTER_KEYS = (wi.WORK_OVERDUE, *wi.DEADLINE_WINDOW_KEYS)


def _deadline(owner, title: str, anchor: date, precision: str, source: str) -> Matter:
    """One real deadline on a Matter of its own: a DO step, or an `Oluline tähtaeg`."""
    matter = factories.MatterFactory(owner=owner, title=title)
    first, last = period_bounds(anchor, precision)
    if source == "step":
        set_next_action(
            matter=matter,
            text=title,
            target_date=first,
            date_precision=precision,
            actor=owner,
            responsible=owner,
        )
    else:
        add_important_date(
            matter=matter,
            title=title,
            date_value=first,
            period_end=last,
            date_precision=precision,
            actor=owner,
        )
    return matter


def _placement(user, matter, today: date) -> Placement:
    # «Kõik tähtajad»: the page's `?kuni=` reach narrows *Hiljem* for a day and a
    # period alike, and is not what is being compared here.
    work = build_my_work(user, today=today, horizon=horizon_from(HORIZON_ALL, today))
    band = next(
        (b.key for b in work.bands for item in b.items if item.matter.pk == matter.pk), None
    )
    items = wi.work_items(user, today=today)
    register = frozenset(
        key
        for key in REGISTER_KEYS
        if matter.pk in wi.work_population_ids(user, key, today=today, items=items)
    )
    osakond = next(
        (
            group.key
            for group in upcoming_groups(user, today, items=items)
            for item in group.items
            if item.matter.pk == matter.pk
        ),
        None,
    )
    thirty = matter.pk in wi.work_population_ids(
        user,
        wi.WORK_DEADLINE_WINDOW,
        today=today,
        items=items,
        window=(today, today + timedelta(days=30)),
    )
    ahead = matter.pk in wi.work_population_ids(
        user, wi.WORK_DEADLINE_WINDOW, today=today, items=items
    )
    overdue = any(item.is_overdue for item in items if item.matter.pk == matter.pk)
    return band, register, osakond, thirty, ahead, overdue


#: A period that has not ended: *Hiljem*, *Tähtaeg kaugemal*, *Kaugemal* — the
#: one category with no last day, on every surface — and in no 30-day count.
LATER: Placement = (
    wi.BAND_LATER,
    frozenset({wi.WORK_DEADLINE_BEYOND}),
    "kaugemal",
    False,
    True,
    False,
)
#: A period that has ended: overdue everywhere, and in no window ahead.
PAST: Placement = (wi.BAND_OVERDUE, frozenset({wi.WORK_OVERDUE}), None, False, False, True)

OCT, Q4, Y27, Y26 = date(2026, 10, 1), date(2026, 10, 1), date(2027, 1, 1), date(2026, 1, 1)
MONTH, QUARTER, YEAR = DatePrecision.MONTH, DatePrecision.QUARTER, DatePrecision.YEAR

BROAD_CASES = [
    # «oktoober 2026»: the day before it begins, its first day, its last, after.
    pytest.param(OCT, MONTH, date(2026, 9, 30), LATER, id="okt-eve"),
    pytest.param(OCT, MONTH, date(2026, 10, 1), LATER, id="okt-1"),
    pytest.param(OCT, MONTH, date(2026, 10, 2), LATER, id="okt-2"),
    pytest.param(OCT, MONTH, date(2026, 10, 31), LATER, id="okt-31"),
    pytest.param(OCT, MONTH, date(2026, 11, 1), PAST, id="okt-ended"),
    # «IV kvartal 2026».
    pytest.param(Q4, QUARTER, date(2026, 9, 30), LATER, id="q4-eve"),
    pytest.param(Q4, QUARTER, date(2026, 10, 1), LATER, id="q4-1"),
    pytest.param(Q4, QUARTER, date(2026, 12, 31), LATER, id="q4-end"),
    pytest.param(Q4, QUARTER, date(2027, 1, 1), PAST, id="q4-ended"),
    # «2027» — never 1.1.2027.
    pytest.param(Y27, YEAR, date(2026, 9, 30), LATER, id="y27-before"),
    pytest.param(Y27, YEAR, date(2026, 12, 29), LATER, id="y27-eve"),
    pytest.param(Y27, YEAR, date(2027, 1, 1), LATER, id="y27-1"),
    pytest.param(Y27, YEAR, date(2027, 2, 10), LATER, id="y27-feb"),
    pytest.param(Y27, YEAR, date(2027, 12, 31), LATER, id="y27-end"),
    pytest.param(Y27, YEAR, date(2028, 1, 1), PAST, id="y27-ended"),
    # «2026» is not overdue in February 2026.
    pytest.param(Y26, YEAR, date(2026, 2, 10), LATER, id="y26-feb"),
]


@pytest.mark.parametrize("source", ["step", "important"])
@pytest.mark.parametrize(("anchor", "precision", "today", "expected"), BROAD_CASES)
def test_a_broad_period_is_one_thing_on_every_surface(
    specialist, source, anchor, precision, today, expected
):
    matter = _deadline(specialist, "Laia perioodi tähtaeg", anchor, precision, source)

    assert _placement(specialist, matter, today) == expected


EXACT_CASES = [
    pytest.param(-1, PAST, id="eile"),
    pytest.param(
        0,
        (wi.BAND_WEEK, frozenset({wi.WORK_DEADLINE_THIS_WEEK}), "tana", True, True, False),
        id="tana",
    ),
    pytest.param(
        1,
        (wi.BAND_WEEK, frozenset({wi.WORK_DEADLINE_THIS_WEEK}), "homme", True, True, False),
        id="homme",
    ),
    pytest.param(
        7,
        (wi.BAND_NEXT_30, frozenset({wi.WORK_DEADLINE_NEXT_WEEK}), "nadal", True, True, False),
        id="jargmisel-nadalal",
    ),
    pytest.param(
        20,
        (wi.BAND_NEXT_30, frozenset({wi.WORK_DEADLINE_30_DAYS}), "kuu", True, True, False),
        id="30-p-jooksul",
    ),
    pytest.param(
        45,
        (wi.BAND_LATER, frozenset({wi.WORK_DEADLINE_BEYOND}), "kaugemal", False, True, False),
        id="kaugemal",
    ),
]


@pytest.mark.parametrize("source", ["step", "important"])
@pytest.mark.parametrize(("offset", "expected"), EXACT_CASES)
def test_an_exact_day_is_still_classified_by_its_day(specialist, source, offset, expected):
    matter = _deadline(
        specialist, "Täpne tähtaeg", TODAY + timedelta(days=offset), DatePrecision.EXACT, source
    )

    assert _placement(specialist, matter, TODAY) == expected


def test_the_day_before_october_every_surface_agrees(specialist):
    """The owner's example, read on 30 September: «oktoober 2026» was *Hiljem* in
    Minu asjad, *Homme* on Osakond and *Tähtaeg sel nädalal* in the register.
    It is the later category on all three; the exact day tomorrow is not."""
    month = _deadline(specialist, "Oktoober", OCT, MONTH, "step")
    day = _deadline(specialist, "Neljapäev", OCT, DatePrecision.EXACT, "step")

    assert _placement(specialist, month, TODAY)[:3] == (
        wi.BAND_LATER,
        frozenset({wi.WORK_DEADLINE_BEYOND}),
        "kaugemal",
    )
    assert _placement(specialist, day, TODAY)[:3] == (
        wi.BAND_WEEK,
        frozenset({wi.WORK_DEADLINE_THIS_WEEK}),
        "homme",
    )


@pytest.mark.parametrize(
    "day",
    [
        date(2026, 9, 30),
        date(2026, 10, 1),
        date(2026, 10, 30),
        date(2026, 12, 31),
        date(2027, 2, 28),
    ],
)
def test_every_open_deadline_is_in_exactly_one_window(specialist, day):
    """The partition `upcoming_windows` promises, now for periods too: a running
    period used to be in no window at all until it became overdue."""
    for n, (anchor, precision) in enumerate(
        [
            (date(2026, 9, 1), MONTH),
            (OCT, MONTH),
            (Q4, QUARTER),
            (Y26, YEAR),
            (Y27, YEAR),
            (day, DatePrecision.EXACT),
            (day + timedelta(days=1), DatePrecision.EXACT),
            (day + timedelta(days=9), DatePrecision.EXACT),
            (day + timedelta(days=25), DatePrecision.EXACT),
            (day + timedelta(days=90), DatePrecision.EXACT),
        ]
    ):
        _deadline(specialist, f"Tähtaeg {n}", anchor, precision, "step")
    items = wi.work_items(specialist, today=day)
    open_ids = sorted(
        (item.object_id for item in wi.real_deadlines(items) if not item.is_overdue), key=str
    )

    groups = upcoming_groups(specialist, day, items=items)
    placed = [item.object_id for group in groups for item in group.items]
    assert sorted(placed, key=str) == open_ids
    register = [
        item.object_id
        for key in wi.DEADLINE_WINDOW_KEYS
        for item in wi.work_population_items(items, key, day)
    ]
    assert sorted(register, key=str) == open_ids


@pytest.mark.parametrize(
    ("anchor", "precision", "words"),
    [(OCT, MONTH, "oktoober 2026"), (Q4, QUARTER, "IV kvartal 2026"), (Y27, YEAR, "2027")],
)
def test_a_broad_period_reads_as_itself_and_never_as_today(specialist, anchor, precision, words):
    """On its anchor day a period is not «täna», has no weekday and is never
    written as ``10.26`` — on the Osakond row and the Minu asjad row alike."""
    _deadline(specialist, "Sõnadega", anchor, precision, "important")

    (item,) = wi.work_items(specialist, today=anchor)

    assert item.is_today is False
    assert item.weekday_letter == ""
    assert item.day_month == words
    assert item.short_date == words
    assert not re.search(r"\d{2}\.\d{2}", item.short_date)


def test_an_exact_day_today_is_still_today(specialist):
    _deadline(specialist, "Täna", TODAY, DatePrecision.EXACT, "important")

    (item,) = wi.work_items(specialist, today=TODAY)

    assert item.is_today is True
    assert item.short_date == "täna"


@pytest.mark.parametrize(
    ("anchor", "precision", "until", "late_from"),
    [
        (OCT, MONTH, date(2026, 10, 31), date(2026, 11, 1)),
        (Q4, QUARTER, date(2026, 12, 31), date(2027, 1, 1)),
        (Y27, YEAR, date(2027, 12, 31), date(2028, 1, 1)),
    ],
)
def test_a_period_is_overdue_only_once_it_has_ended(
    specialist, anchor, precision, until, late_from
):
    """The step's own state, the work model and the register's
    `?tegevus=hilinenud` agree: not late through its last day, late the next."""
    matter = _deadline(specialist, "Hilinemine", anchor, precision, "step")
    action = NextAction.objects.get(matter=matter)

    for day, late in ((anchor, False), (until, False), (late_from, True)):
        assert action.is_overdue(day) is late, day
        assert (wi.WORK_OVERDUE in _placement(specialist, matter, day)[1]) is late, day
        listed = register_population(
            specialist, {"tegevus": "hilinenud"}, today=day, population=Matter.objects.all()
        )
        assert listed.filter(pk=matter.pk).exists() is late, day


def test_the_register_and_osakond_pages_agree(signed_in, department_head, specialist):
    """Through the pages themselves, on the real clock: a period running now is
    in *Tähtaeg kaugemal* in the register and under *Kaugemal* on Osakond, and
    in no window with a last day; a deadline today is today's on both."""
    today = timezone.localdate()
    month = _deadline(specialist, "Käesolev kuu", today, MONTH, "step")
    day = _deadline(specialist, "Täpselt täna", today, DatePrecision.EXACT, "step")

    def listed(key):
        body = signed_in.get(
            reverse("matters:matter_list") + f"?olek=avatud&liik=FULL&too={key}"
        ).content.decode()
        return {matter.title for matter in (month, day) if matter.title in body}

    assert listed(wi.WORK_DEADLINE_BEYOND) == {"Käesolev kuu"}
    assert listed(wi.WORK_DEADLINE_THIS_WEEK) == {"Täpselt täna"}
    assert listed(wi.WORK_DEADLINE_NEXT_WEEK) == set()
    assert listed(wi.WORK_DEADLINE_30_DAYS) == set()

    signed_in.force_login(department_head)
    body = signed_in.get(reverse("matters:department")).content.decode()
    windows = dict(
        re.findall(
            r'<details class="uxdl">.*?uxdl__title[^>]*>([^<]+)<.*?</summary>(.*?)</details>',
            body,
            re.S,
        )
    )
    assert "Käesolev kuu" in windows["KAUGEMAL"]
    assert "Käesolev kuu" not in windows["TÄNA"]
    assert "Täpselt täna" in windows["TÄNA"]


@pytest.mark.parametrize(
    ("value", "precision", "start", "end", "inside"),
    [
        # A day, both ends inclusive, and an open end.
        (OCT, DatePrecision.EXACT, OCT, OCT, True),
        (date(2026, 10, 2), DatePrecision.INFERRED, OCT, OCT, False),
        (date(2026, 12, 1), DatePrecision.EXACT, date(2026, 11, 1), None, True),
        # A period is never in a window with a last day — not even one that
        # happens to contain every day of it.
        (OCT, MONTH, OCT, date(2026, 10, 31), False),
        (OCT, MONTH, date(2026, 9, 1), date(2027, 12, 31), False),
        # The open window beginning later holds every period not yet ended…
        (date(2026, 9, 1), MONTH, date(2026, 11, 1), None, True),
        (Y27, YEAR, date(2026, 11, 1), None, True),
        # …and not one that has.
        (date(2026, 8, 1), MONTH, date(2026, 11, 1), None, False),
        # One opening in the past holds the periods that reach it.
        (date(2026, 8, 1), MONTH, date(2026, 8, 15), None, True),
        (date(2026, 7, 1), MONTH, date(2026, 8, 15), None, False),
        (None, DatePrecision.EXACT, date(2026, 1, 1), None, False),
    ],
)
def test_the_window_rule(value, precision, start, end, inside):
    assert period_in_window(value, precision, start=start, end=end, today=TODAY) is inside


# ---------------------------------------------------------------------------
# 3 — no raw control byte in production source
# ---------------------------------------------------------------------------

#: What a control byte in source text looks like: everything below the space
#: except tab, line feed and carriage return, and DEL.
CONTROL_BYTE = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

#: The trees that ship in the image. `tests/` and `e2e/` still carry four of
#: these (docs/adr/0122 §3 lists them) and are deliberately not in scope here.
PRODUCTION_TREES = ("app", "config", "templates", "static", "scripts", "deploy")
TEXT_SUFFIXES = {".py", ".html", ".css", ".js", ".sh", ".txt", ".toml", ".yml", ".yaml", ".md"}


def test_production_source_carries_no_raw_control_byte():
    """`\\b` typed through a shell that reads it as a backspace is how
    `opinion_sources.ADDRESSEE_SEPARATOR` came to hold two 0x08 bytes: the file
    compiled, the tests passed, and «ning» never separated anything. A byte like
    that is invisible in review, so the tree is read for it instead."""
    offenders = []
    for tree in PRODUCTION_TREES:
        for path in (REPO / tree).rglob("*"):
            if path.suffix not in TEXT_SUFFIXES or not path.is_file():
                continue
            for number, line in enumerate(path.read_bytes().split(b"\n"), start=1):
                if CONTROL_BYTE.search(line):
                    offenders.append(f"{path.relative_to(REPO)}:{number}")

    assert offenders == []


def test_ning_separates_addressees_as_a_whole_word():
    from app.legacy_import.opinion_sources import ADDRESSEE_SEPARATOR, addressee_bodies, fold
    from app.legacy_import.register_semantics import _ADDRESSEE_SEPARATOR

    # The same pattern the register's own reading of the column uses.
    assert ADDRESSEE_SEPARATOR.pattern == _ADDRESSEE_SEPARATOR.pattern == r"[,;/]|\bning\b"
    both = addressee_bodies("Justiitsministeerium ning Siseministeerium")
    assert {fold("Justiitsministeerium"), fold("Siseministeerium")} <= both
    # Inside a word it is not a separator.
    assert addressee_bodies("Kuninglik Kolledž") == frozenset({fold("Kuninglik Kolledž")})
    # The separators that always worked still do, and the whole string stays a key.
    assert addressee_bodies("Siseministeerium, HTM") >= {
        fold("Siseministeerium, HTM"),
        fold("Siseministeerium"),
    }
