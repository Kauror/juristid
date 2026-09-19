from __future__ import annotations

from django.db import models


class ChangeEventType(models.TextChoices):
    """Authoritative business changes shown in the professional timeline.

    Stage 0 seeds only the events the foundational schema can already produce;
    Stage 1 adds the workflow events (stage change, next action, submission).
    """

    MATTER_CREATED = "MATTER_CREATED", "Teema loodud"
    MATTER_ASSIGNED = "MATTER_ASSIGNED", "Teema määratud"
    # The Matter's own name. Its own event rather than a reused one, for the
    # reason every other field here has its own: the title is what everybody
    # navigates and cites by, so a rename is the one change most likely to make
    # a colleague think they are looking at a different file — and a history
    # that called it "hetkeseis muudetud" could not answer who renamed it. The
    # payload carries both strings, unlike `Lühikokkuvõte`, because the old
    # title is how somebody finds a Matter again after it stopped being called
    # what they remember (Teema QA §2.4).
    MATTER_TITLE_CHANGED = "MATTER_TITLE_CHANGED", "Pealkiri muudetud"
    MATTER_STAGE_CHANGED = "MATTER_STAGE_CHANGED", "Hetkeseis muudetud"
    MATTER_TRACK_CHANGED = "MATTER_TRACK_CHANGED", "Menetlusliik muudetud"
    MATTER_ORGANISATION_CHANGED = "MATTER_ORGANISATION_CHANGED", "Asutus muudetud"
    MATTER_DATE_CHANGED = "MATTER_DATE_CHANGED", "Kuupäev muudetud"
    MATTER_POSITION_UPDATED = "MATTER_POSITION_UPDATED", "Seisukohta täiendatud"
    # The plain-language `Lühikokkuvõte`. Its own event, for the same reason
    # MATTER_POLICY_AREA_OTHER_SET has one: it is not a position, not a stage
    # and not taxonomy, and a history that called it "seisukohta täiendatud"
    # would describe a change that did not happen (Teema redesign §6.1).
    MATTER_BRIEF_SUMMARY_SET = "MATTER_BRIEF_SUMMARY_SET", "Lühikokkuvõte muudetud"
    # Which Valdkonnad a Matter is filed under. Distinct from the free-text
    # `Muu valdkond` beside it: this one moves canonical taxonomy relations that
    # every statistic is cut along, and the payload names the rows that moved.
    MATTER_POLICY_AREAS_CHANGED = "MATTER_POLICY_AREAS_CHANGED", "Valdkonnad muudetud"
    # Stage 2E.1. Its own event rather than a reused one: the free-text area
    # is not a position, not a stage and not taxonomy, and a timeline that
    # called it any of those would be describing a change that did not happen.
    MATTER_POLICY_AREA_OTHER_SET = "MATTER_POLICY_AREA_OTHER_SET", "Muu valdkond muudetud"
    # Which Õigusakt a Matter concerns. Its own event beside the Valdkonnad
    # pair above rather than a shared "classification changed", for the reason
    # every other field here has its own: Menetlusliik, Valdkond and Õigusakt
    # are three independent answers, and a history that could not say which of
    # them somebody corrected would be a history nobody trusts (docs/adr/0070).
    MATTER_LEGAL_INSTRUMENTS_CHANGED = "MATTER_LEGAL_INSTRUMENTS_CHANGED", "Õigusakt muudetud"
    # And the free text beside it, which is not taxonomy and moves no relation —
    # the same split MATTER_POLICY_AREA_OTHER_SET makes for the same reason.
    MATTER_LEGAL_INSTRUMENT_OTHER_SET = (
        "MATTER_LEGAL_INSTRUMENT_OTHER_SET",
        "Muu õigusakt muudetud",
    )
    MATTER_VISIBILITY_CHANGED = "MATTER_VISIBILITY_CHANGED", "Nähtavus muudetud"
    # Real business data, or a record made while developing. Its own event
    # rather than a reused field-change one, for the same reason as the two
    # above it: a history that called this "kuupäev muudetud" could not answer
    # who decided that a record was never about anything. Deliberately absent
    # from `matters.timeline.TIMELINE_EVENT_TYPES`, beside
    # MATTER_VISIBILITY_CHANGED — reclassifying a record is data management, not
    # authored chronology, and it would sit in the narrative saying nothing
    # about the policy work (Agent-C brief 19).
    MATTER_DATA_CLASS_CHANGED = "MATTER_DATA_CLASS_CHANGED", "Andmeklass muudetud"
    MATTER_CLOSED = "MATTER_CLOSED", "Teema suletud"
    # A Teema that was deleted. Its own type, and the one event that describes a
    # record which is no longer there: every owned business row is gone and the
    # `Matter` row survives only because this row points at it under `PROTECT`
    # onto an append-only table. Deliberately absent from
    # `matters.timeline.TIMELINE_EVENT_TYPES` — there is no chronology left to
    # render it on (docs/adr/0096 §4).
    MATTER_DELETED = "MATTER_DELETED", "Teema kustutatud"
    MATTER_REOPENED = "MATTER_REOPENED", "Teema taasavatud"
    # An archive register record activated as current work. Distinct from
    # MATTER_CREATED — nothing was created, the identity and the provenance are
    # the ones the register already had — and from MATTER_REOPENED, which is
    # about a closure being undone (master specification 19.4).
    MATTER_PROMOTED = "MATTER_PROMOTED", "Arhiivikirjest aktiivne teema"
    NEXT_ACTION_SET = "NEXT_ACTION_SET", "Järgmiseks määratud"
    NEXT_ACTION_COMPLETED = "NEXT_ACTION_COMPLETED", "Järgmiseks tehtud"
    NEXT_ACTION_CANCELLED = "NEXT_ACTION_CANCELLED", "Järgmiseks tühistatud"
    NEXT_ACTION_REVIEWED = "NEXT_ACTION_REVIEWED", "Järgmiseks üle vaadatud"
    ENTRY_ADDED = "ENTRY_ADDED", "Sissekanne lisatud"
    ENTRY_EDITED = "ENTRY_EDITED", "Sissekannet muudetud"
    SUBMISSION_CREATED = "SUBMISSION_CREATED", "Arvamus loodud"
    SUBMISSION_SENT = "SUBMISSION_SENT", "Arvamus välja saadetud"
    SUBMISSION_WITHDRAWN = "SUBMISSION_WITHDRAWN", "Arvamus tagasi võetud"
    SUBMISSION_SUPERSEDED = "SUBMISSION_SUPERSEDED", "Arvamus asendatud"
    SUBMISSION_RECIPIENTS_CHANGED = "SUBMISSION_RECIPIENTS_CHANGED", "Arvamuse saajad muudetud"
    # -- docs/adr/0093: what one letter argued about, and where it was written up
    #
    # Four types rather than two, and separate from `TAG_ASSIGNED` / `TAG_REMOVED`
    # directly below. Those two say a *Matter* was classified; these say a
    # *Submission* was, and a history that could not tell the file's subject from
    # one letter's subject would be a history nobody can answer a question with —
    # the reasoning every field-level type in this enum already follows.
    #
    # One per fact that moved: adding two keywords and removing one is three rows,
    # exactly as `set_tags` has always written three for a Matter. A save that
    # changed nothing writes none of them.
    #
    # All four are deliberately absent from `matters.timeline.TIMELINE_EVENT_TYPES`,
    # beside `TAG_ASSIGNED` and for the same reason: classifying a letter is data
    # management rather than authored chronology, and echoing it into the
    # professional narrative would bury what actually happened to the file.
    SUBMISSION_TAG_ASSIGNED = "SUBMISSION_TAG_ASSIGNED", "Arvamuse märksõna lisatud"
    SUBMISSION_TAG_REMOVED = "SUBMISSION_TAG_REMOVED", "Arvamuse märksõna eemaldatud"
    SUBMISSION_OVERVIEW_LINKED = (
        "SUBMISSION_OVERVIEW_LINKED",
        "Arvamus seotud ülevaate või uudisega",
    )
    SUBMISSION_OVERVIEW_UNLINKED = (
        "SUBMISSION_OVERVIEW_UNLINKED",
        "Arvamuse seos ülevaate või uudisega eemaldatud",
    )
    DOCUMENT_CREATED = "DOCUMENT_CREATED", "Dokument loodud"
    EVIDENCE_VERSION_ADDED = "EVIDENCE_VERSION_ADDED", "Tõendiversioon lisatud"
    TAG_ASSIGNED = "TAG_ASSIGNED", "Silt lisatud"
    TAG_REMOVED = "TAG_REMOVED", "Silt eemaldatud"
    IMPORT_APPLIED = "IMPORT_APPLIED", "Import rakendatud"
    # -- Stage 2G: structured Matter facts ---------------------------------
    #
    # Their own event types rather than a reused MATTER_DATE_CHANGED. A watched
    # milestone, a commencement date and a claimed work victory are three
    # different facts, and a history that called all of them "kuupäev muudetud"
    # could not answer which one somebody changed. They are deliberately absent
    # from `matters.timeline.TIMELINE_EVENT_TYPES`: adding a structured fact is
    # not authored chronology, and echoing each one into the professional
    # narrative would bury the meeting notes (Stage-2G brief 34, 37).
    IMPORTANT_DATE_ADDED = "IMPORTANT_DATE_ADDED", "Oluline tähtaeg lisatud"
    IMPORTANT_DATE_CHANGED = "IMPORTANT_DATE_CHANGED", "Olulist tähtaega muudetud"
    IMPORTANT_DATE_CANCELLED = "IMPORTANT_DATE_CANCELLED", "Oluline tähtaeg tühistatud"
    EFFECTIVE_DATE_ADDED = "EFFECTIVE_DATE_ADDED", "Jõustumine lisatud"
    EFFECTIVE_DATE_CHANGED = "EFFECTIVE_DATE_CHANGED", "Jõustumist muudetud"
    EFFECTIVE_DATE_CANCELLED = "EFFECTIVE_DATE_CANCELLED", "Jõustumine tühistatud"
    WORK_VICTORY_PROPOSED = "WORK_VICTORY_PROPOSED", "Töövõidu kandidaat lisatud"
    WORK_VICTORY_CHANGED = "WORK_VICTORY_CHANGED", "Töövõidu kirjet muudetud"
    WORK_VICTORY_CONFIRMED = "WORK_VICTORY_CONFIRMED", "Töövõit kinnitatud"
    WORK_VICTORY_REJECTED = "WORK_VICTORY_REJECTED", "Töövõit ei realiseerunud"
    # -- Stage 2I: the historical cutover -----------------------------------
    #
    # Deliberately not `MATTER_CLOSED`. That event means a person closed active
    # work today, and it carries a real disposition and a real timestamp. This
    # one means something weaker and stranger: at the cutover the department
    # decided that a pre-2026 register row is no longer current, while the date
    # activity actually stopped and the reason it stopped remain unknown and
    # are not invented.
    #
    # The event's own timestamp is when the normalisation ran, which is why it
    # stays out of `matters.timeline.TIMELINE_EVENT_TYPES`: a line in the
    # chronology reading "closed" on the cutover day would assert precisely the
    # fact this operation refuses to claim (Stage-2I brief 8, 20).
    MATTER_HISTORICAL_CUTOVER_CLOSED = (
        "MATTER_HISTORICAL_CUTOVER_CLOSED",
        "Ajalooline kirje: enam mitte jooksev töö",
    )
    # -- the final register cutover ------------------------------------------
    #
    # A third kind of "no longer current", and it needs its own name for the
    # same reason the second one did. `MATTER_CLOSED` is a person closing live
    # work with a disposition and a timestamp. `MATTER_HISTORICAL_CUTOVER_CLOSED`
    # is the year-only interim rule retiring a pre-2026 row about which the
    # register said nothing. This one is narrower and better evidenced: the
    # final maintained snapshot carries a terminal `HETKESEIS` for this exact
    # Matter, or says its work continues under a named other one.
    #
    # It still claims no closure date, no disposition and no closing person,
    # because the register records none — so it stays out of the timeline
    # alongside its predecessor, for the reason given there.
    MATTER_REGISTER_CUTOVER_RETIRED = (
        "MATTER_REGISTER_CUTOVER_RETIRED",
        "Lõpliku registri järgi enam mitte jooksev töö",
    )
    MATTER_REGISTER_CUTOVER_ACTIVATED = (
        "MATTER_REGISTER_CUTOVER_ACTIVATED",
        "Lõpliku registri järgi jooksev töö",
    )
    # The source refresh. Its payload names which fields moved and to what, so
    # the change is auditable without the register's own text being copied into
    # an audit row — that text stays on the immutable source reference.
    MATTER_SOURCE_FIELDS_REFRESHED = (
        "MATTER_SOURCE_FIELDS_REFRESHED",
        "Väljad uuendatud registri põhjal",
    )
    # -- Wave 2: Kaasamine --------------------------------------------------
    #
    # Their own event types. `ENTRY_ADDED` would claim somebody wrote a note,
    # `MATTER_DATE_CHANGED` would claim a Matter field moved, and
    # `IMPORT_APPLIED` would claim an importer did it — none of the three is
    # true of a person recording that the Chamber asked its members something.
    #
    # Deliberately absent from `matters.timeline.TIMELINE_EVENT_TYPES`, like the
    # Stage-2G structured facts above: the Kaasamine section already shows the
    # fact in a form a reader can act on, and echoing each one into the
    # professional narrative is exactly the noise that architecture avoids
    # (Agent-F brief 20, 22).
    ENGAGEMENT_ADDED = "ENGAGEMENT_ADDED", "Kaasamine lisatud"
    ENGAGEMENT_CHANGED = "ENGAGEMENT_CHANGED", "Kaasamist muudetud"
    # `Lõpeta kaasamine` — the round stopped waiting for answers.
    #
    # Its own event rather than an `ENGAGEMENT_CHANGED` naming two more moved
    # columns, because it is the one act on this record that a reader audits.
    # «Who decided this consultation was finished, when, and what did they say
    # had come back» is a question about a *decision*, and a history that buried
    # it in a sorted list of field names could not answer it — the reasoning
    # `EXTERNAL_POSITION_SOURCE_CHANGED` already carries one model along
    # (docs/adr/0086 §6).
    #
    # The payload names the closure's reason — a person, or the Matter shutting
    # underneath the wait — and says whether anything was written down, never
    # what was written. Feedback runs to paragraphs and is on the record where
    # it can be corrected; an audit table holding a second copy of it would be
    # a worse copy nobody maintains (Agent-F brief 26).
    #
    # Deliberately absent from `matters.timeline.TIMELINE_EVENT_TYPES`, like
    # every other structured fact since docs/adr/0074 §14: the chronology
    # renders the completed round from the record itself, and reading the event
    # as well would state one act twice.
    ENGAGEMENT_FEEDBACK_CLOSED = "ENGAGEMENT_FEEDBACK_CLOSED", "Kaasamise tagasiside laekunud"
    # Seotud materjalid: the four human decisions the section records. A
    # dismissal («Ei ole seotud») keeps its actor and time on its own row and
    # writes no event, because it is a preference about what to suggest rather
    # than a fact about the file (docs/adr/0062).
    MATTER_RELATION_ADDED = "MATTER_RELATION_ADDED", "Teema seotud teise teemaga"
    MATTER_RELATION_REMOVED = "MATTER_RELATION_REMOVED", "Teemade seos eemaldatud"
    BACKGROUND_MATERIAL_ADDED = "BACKGROUND_MATERIAL_ADDED", "Taustmaterjal lisatud"
    BACKGROUND_MATERIAL_REMOVED = "BACKGROUND_MATERIAL_REMOVED", "Taustmaterjal eemaldatud"
    # -- Ülevaade / uudis ---------------------------------------------------
    #
    # Four events, because the record has four things that can happen to it and
    # a history that could not tell them apart would be a history nobody trusts.
    # `ENGAGEMENT_ADDED` would claim the Chamber asked somebody something,
    # `SUBMISSION_SENT` would claim an opinion went out, and a shared
    # "overview changed" could not answer whether a plan was dropped or an
    # address corrected (docs/adr/0081).
    #
    # **The four stored values keep their `WEBSITE_OVERVIEW_` spelling**, and
    # deliberately: they are written into `ChangeEvent.event_type` on every row
    # this record has ever produced, and renaming a stored value is a data
    # migration in return for a word nobody reads. The *labels* are what a human
    # sees, and those say `Ülevaade / uudis` (docs/adr/0085 §5).
    #
    # The published and the cancelled ones are the two *milestones*, and unlike
    # every other structured fact on the Matter they are deliberately absent
    # from `matters.timeline.TIMELINE_EVENT_TYPES` for the usual reason: the
    # chronology renders those two from the canonical record itself, through
    # `projected_milestones`, so reading the event as well would state one act
    # twice (docs/adr/0074 §14).
    WEBSITE_OVERVIEW_PLANNED = "WEBSITE_OVERVIEW_PLANNED", "Ülevaade / uudis plaanis"
    WEBSITE_OVERVIEW_PUBLISHED = "WEBSITE_OVERVIEW_PUBLISHED", "Ülevaade / uudis avaldatud"
    WEBSITE_OVERVIEW_CANCELLED = "WEBSITE_OVERVIEW_CANCELLED", "Ülevaade / uudis tühistatud"
    # A correction to an address or a publication date that was already
    # recorded. Its own event rather than a second PUBLISHED one, because the
    # first says a page appeared and this one says the file was
    # wrong about where or when — and a closed Matter accepts the second while
    # refusing the first.
    WEBSITE_OVERVIEW_LINK_CORRECTED = (
        "WEBSITE_OVERVIEW_LINK_CORRECTED",
        "Ülevaate või uudise linki või kuupäeva parandatud",
    )
    # -- Väline seisukoht ---------------------------------------------------
    #
    # Four events, because four different things happen to this record and a
    # history that could not tell them apart would be a history nobody trusts.
    # None of the existing families would have been true of any of them:
    # `ENGAGEMENT_ADDED` would claim the Chamber asked somebody something,
    # `SUBMISSION_SENT` would claim an opinion went out, `ENTRY_ADDED` would
    # claim somebody wrote a note, and `WORK_VICTORY_PROPOSED` would claim
    # something was won — where what happened is that another organisation
    # stated a position and the file now points at it (docs/adr/0084 §7).
    #
    # Deliberately absent from `matters.timeline.TIMELINE_EVENT_TYPES`, like
    # every other structured fact since docs/adr/0074 §14: the chronology
    # renders the position from the canonical record through
    # `projected_milestones`, and reading the event as well would state one act
    # twice.
    EXTERNAL_POSITION_RECORDED = "EXTERNAL_POSITION_RECORDED", "Väline seisukoht lisatud"
    EXTERNAL_POSITION_CORRECTED = "EXTERNAL_POSITION_CORRECTED", "Välist seisukohta parandatud"
    # Its own event beside the correction rather than a field named inside it.
    # The source is what makes this record a record at all — a position with no
    # link and no document is hearsay on a file — so «where this points now, and
    # where it pointed before» is the one change a reader is most likely to be
    # auditing, and a history that buried it in a list of moved field names
    # could not answer it.
    EXTERNAL_POSITION_SOURCE_CHANGED = (
        "EXTERNAL_POSITION_SOURCE_CHANGED",
        "Välise seisukoha link muudetud",
    )
    # The other half of the source. `DOCUMENT_CREATED` and
    # `EVIDENCE_VERSION_ADDED` already record that bytes arrived; this records
    # that they are the evidence for *this* position, which is the fact the
    # `DocumentLink` row carries and neither of those two can state.
    EXTERNAL_POSITION_DOCUMENT_LINKED = (
        "EXTERNAL_POSITION_DOCUMENT_LINKED",
        "Välise seisukoha fail lisatud",
    )
    # -- Menetluse link -----------------------------------------------------
    #
    # Two events, because two things happen to this record: an address is
    # recorded, and a recorded address or its classification turns out to be
    # wrong. Nothing else can: there is no deletion and no lifecycle
    # (docs/adr/0089 §6).
    #
    # None of the existing families would have been true of either.
    # `EXTERNAL_POSITION_RECORDED` would claim another organisation stated a
    # position; `WEBSITE_OVERVIEW_PUBLISHED` would claim this file was written
    # up for the public; `ENTRY_ADDED` would claim somebody wrote a note. What
    # happened is that a lawyer recorded where the official proceeding lives,
    # which is a fact about the procedure and not about anybody's opinion of it
    # (docs/adr/0089 §5).
    #
    # Deliberately absent from `matters.timeline.TIMELINE_EVENT_TYPES`, like
    # every other structured fact since docs/adr/0074 §14 — and here for a
    # second reason as well: a procedural link is not something that *happened*
    # on the file, it is where the file is happening. A chronology row saying
    # «EIS link added» on 17 September would be the history of somebody's typing
    # rather than the history of the proceeding (docs/adr/0089 §11).
    PROCEDURAL_LINK_RECORDED = "PROCEDURAL_LINK_RECORDED", "Menetluse link lisatud"
    PROCEDURAL_LINK_CORRECTED = "PROCEDURAL_LINK_CORRECTED", "Menetluse linki parandatud"

    # `Menetluse areng` — one step the external procedure took. Three events,
    # because three different things happen to this record and no more: it is
    # recorded, it is corrected, and bytes are named as the evidence for it.
    #
    # None of the three is in `TIMELINE_EVENT_TYPES`: the chronology renders the
    # development from the canonical record through `projected_milestones`, and
    # reading the event as well would state one act twice — the rule every
    # structured fact on this page has kept since docs/adr/0074 §14.
    PROCEDURAL_DEVELOPMENT_RECORDED = (
        "PROCEDURAL_DEVELOPMENT_RECORDED",
        "Menetluse areng lisatud",
    )
    PROCEDURAL_DEVELOPMENT_CORRECTED = (
        "PROCEDURAL_DEVELOPMENT_CORRECTED",
        "Menetluse arengut parandatud",
    )
    # `DOCUMENT_CREATED` and `EVIDENCE_VERSION_ADDED` already record that bytes
    # arrived on the Matter; neither can say that they are the ministry's revised
    # draft rather than something else that turned up the same afternoon.
    PROCEDURAL_DEVELOPMENT_DOCUMENT_LINKED = (
        "PROCEDURAL_DEVELOPMENT_DOCUMENT_LINKED",
        "Menetluse arengu fail lisatud",
    )


class SecurityEventType(models.TextChoices):
    """Access, permission and administrative trace, separate from the timeline."""

    BREAK_GLASS_GRANTED = "BREAK_GLASS_GRANTED", "Hädaligipääs antud"
    BREAK_GLASS_REVOKED = "BREAK_GLASS_REVOKED", "Hädaligipääs tühistatud"
    BREAK_GLASS_USED = "BREAK_GLASS_USED", "Hädaligipääsu kasutati"
    RESTRICTED_RECORD_ACCESSED = "RESTRICTED_RECORD_ACCESSED", "Piiratud kirjet vaadati"
    DOCUMENT_DOWNLOADED = "DOCUMENT_DOWNLOADED", "Dokument alla laaditud"
    EXPORT_GENERATED = "EXPORT_GENERATED", "Väljavõte koostatud"
    IMPORT_RUN = "IMPORT_RUN", "Import käivitatud"
    ROLE_CHANGED = "ROLE_CHANGED", "Roll muudetud"
    AUTHENTICATION_SUCCEEDED = "AUTHENTICATION_SUCCEEDED", "Sisselogimine õnnestus"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED", "Sisselogimine ebaõnnestus"
    # -- the shared gate ---------------------------------------------------
    #
    # Kept distinct from AUTHENTICATION_SUCCEEDED on purpose. Passing a shared
    # password is not somebody signing in; recording it as if it were would put
    # a claim in the audit trail that the deployment cannot support
    # (docs/adr/0016).
    SHARED_GATE_PASSED = "SHARED_GATE_PASSED", "Jagatud parool sisestati"
    SHARED_GATE_CLOSED = "SHARED_GATE_CLOSED", "Jagatud seanss lõpetati"
    PERSONA_SELECTED = "PERSONA_SELECTED", "Kasutajavaade valiti"
