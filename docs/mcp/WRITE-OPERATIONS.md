# MCP write operations

The ten changes an AI client may **propose** to Juristid, what each one asks
for, which canonical service it ends in, and what it never does. The life of a
proposal — prepare, the person's approval in Juristid with their own
authenticator, commit — is the gateway's (CONTRACT §5, ADR 0189); the
interface every operation implements is `app/mcp_writes/operations/base.py`;
the operations live in `app/mcp_writes/operations/` and are registered in
`REGISTRY` (`operations/__init__.py`). The MCP tool for an operation is
`prepare_<name>` and its input schema **is** the operation's pydantic model.

## Rules every operation keeps

| Rule | How |
| --- | --- |
| The principal is the verified token's user — never an argument | No model has a user, persona, e-mail, role or upn field (`test_every_argument_model_is_closed_bounded_and_described`). |
| Only a business writer | `may_write_business_content` in `prepare` **and** again in `execute`: READER, ADMINISTRATOR and a superuser without a business role get `forbidden`. |
| Invisible == not found | The Matter is resolved through the read tools' resolver (`app.mcp_server.readers.resolve_matter`: reference `2026_225`, UUID, or `matter:<uuid>`) over `Matter.objects.visible_to(user)`; `execute` re-reads it through `visible_to`. |
| Closed or archived Matter | Refused as `domain_rule` before anything is prepared, and again under the Matter lock at execute (`lock_open_matter_for_business_write`). This is stricter than the web in at least one place (`assign_matter` lets a closed Matter change hands): MCP writes only open `FULL` Matters. |
| Arguments | `extra="forbid"`, every string bounded (free text by `MCP_WRITE_MAX_TEXT_CHARS`, others by the column/form limit), explicit enums, dates as ISO strings at an explicit precision. Malformed input is `invalid_argument`. |
| Nothing invented | A missing fact is `missing_information` naming what is missing; no day, outcome, deadline, stage or person is defaulted except where the web door defaults it (create: you are the Vastutaja when you name nobody). |
| `prepare` writes nothing | Proven by the SQL it sends (`no_writes` in `tests/test_mcp_write_operations_support.py`), not only by row counts. |
| Staleness | `state` is recomputed under the canonical Matter lock at commit and must equal the stored one. Every state carries the Matter's visibility to the user, the user's write right, `is_open`, `record_mode`, deletion, plus the operation's own facts below. |
| Canonical services only | `execute` calls the service the web door calls; a `DomainError` becomes `OperationRefused("domain_rule", <the service's own sentence>)`. Save-once services receive the proposal id as `once_token` (docs/adr/0167). |
| Plain text in, escaped | Chronology text is HTML-escaped and turned into `<p>`/`<br>` (`common.text_to_entry_html`) before `add_entry` sanitises it, so a `<script>` in the text is stored as words. |
| Dates | `exact` `YYYY-MM-DD`, `month` `YYYY-MM`, `quarter` `YYYY-Qn`, `year` `YYYY`. A value written at another precision is refused (a month is never read out of a day). The stored anchor is the period's first day, as the forms store it; the range is 1990–2100. `HALF_YEAR` and `INFERRED` are not offered for new input (docs/adr/0079). |

## The operations

### `create_matter` — «Uus teema»

- **Arguments:** `title` (1–1000, required), `brief_summary` (optional),
  `responsible_person` (optional display name of an assignable colleague; an
  unambiguous first name also resolves; two matches → `missing_information`
  with both names).
- **Service:** `app.matters.intake.file_new_matter(title, owner, brief_summary,
  visibility=NORMAL, data_class=REAL, seed_plan=True)` — the one creation
  sequence `Uus teema` and `Saabunud` share; the standard plan (suggestions, not
  tasks) is seeded as `Uus teema` seeds it.
- **Permission:** writer; naming somebody else needs `may_assign_work`
  (`work.assign`); naming oneself is capture. Omitted → the creator when they are
  an assignable department worker (`creator_if_nameable`), otherwise nobody.
- **State:** `may_write`, `may_assign_work`, `owner_id`, `owner_assignable`.
- **Never:** an opinion deadline, a Hetkeseis, a received date, senders,
  instruments, a next action, an outcome, files, a restricted visibility.
  A same-title open Matter the user can see is a preview **warning**, not a
  refusal. «Exactly once» is the gateway's proposal status; `file_new_matter`
  has no save-once token (the web door's intake-session token does not apply).

### `update_matter` — title and «Lühikokkuvõte»

- **Arguments:** `matter`, `title`, `brief_summary` (replaces the whole
  summary), `clear_brief_summary`; at least one; summary and clear are exclusive;
  equal-to-current is `invalid_argument`.
- **Service:** `set_matter_title`, `set_brief_summary` under
  `lock_open_matter_for_business_write`.
- **State:** `title`, `brief_summary_digest` (SHA-256 prefix, not the text).
- **Never:** reference, owner, stage, dates or steps.

### `assign_matter` — Vastutaja

- **Arguments:** `matter`, `action` `assign`|`clear`, `responsible_person`
  (with `assign`). Clearing is allowed because the header allows it.
- **Service:** `assign_matter` (moves the open and planned steps that were
  following the previous owner; raises the «Uus asi» notice for somebody else).
- **Permission:** writer + `may_assign_work`, also when the person is oneself
  (the service refuses any change without it).
- **State:** `owner_id`, `may_assign_work`, `proposed_owner_assignable`.
- **Never:** step text or dates, stage, deadlines.

### `add_matter_note` — shared chronology «Muu»

- **Arguments:** `matter`, `text`, `occurred_on` (required, not after today in
  Europe/Tallinn), `source_url` (optional; `normalize_entry_url`: http/https,
  no credentials, ≤ 1000).
- **Service:** `workspace.add_matter_other(..., once_token=proposal id)` →
  one `Entry(kind=OTHER)` + `ENTRY_ADDED`.
- **Never:** a private personal note (`MatterPersonalNote`), a next action, a
  completion, a closure, a fetch of the URL.

### `record_meeting` — «Kohtumine / arutelu»

- **Arguments:** `matter`, `summary` (participants are named in it), `held_on`
  (required, not after today).
- **Service:** `workspace.add_matter_meeting(..., once_token=proposal id)` →
  `Entry(kind=MEETING)`.
- **Never:** a Koja arvamus, a `Submission`, a `MatterEngagement`, a feedback
  wait, a step.

### `add_procedural_link` — «Menetluse link»

- **Arguments:** `matter`, `kind` `eis`|`ministry_register`|`eu_procedure`|
  `riigikogu`|`other` (`ProceduralLinkKind`), `url`, `label` (≤ 120).
- **Service:** `workspace.add_matter_procedural_link` → `record_procedural_link`.
- **Refused:** an address the Matter already holds (same answers: «nothing would
  change»; different kind/label: the service's `PROCEDURAL_LINK_DUPLICATE`).
- **State:** `address_recorded`.
- **Never:** fetches, downloads, documents, scheduled work.

### `set_next_action` — the current step

- **Arguments:** `matter`, `mode` `new`|`change_current`, `current_action_id`
  (optional, `change_current` only), `text` (≤ 2000), `kind` `do`|`wait`|`monitor`,
  `date` + `date_precision` (both or neither), `date_meaning`
  (`do` → `planned_day` = `DEADLINE`; `wait`/`monitor` → `review_on` = `REVIEW_ON`
  or `expected_around` = `EXPECTED_AROUND`, required when dated), `waiting_for`
  (required for `wait`).
- **Who is waited for:** `NextAction` has no column for it, so it is carried in
  the step's sentence: when the sentence does not already name the party,
  « (ootame: <party>)» is appended — shown verbatim in the preview.
- **Service:** `new` → `set_next_action_for_new_work` under
  `lock_open_matter_for_business_write`, refused when a current step exists
  (never a silent supersession, unlike `+ Määra järgmine tegevus` which files a
  dated step as planned — not offered here). `change_current` →
  `workspace.change_current_action` (named step, re-read under lock; plan step
  and restriction carried; an opinion follow-up check is refused with
  `FOLLOW_UP_HAS_ITS_OWN_FORM`).
- **Responsibility:** the Matter's owner, refused with `DEPARTED_OWNER_REFUSAL`
  when the owner is no longer an assignable worker (`responsible_for_new_work`).
- **State:** `owner_id`, `owner_assignable`, `current_action`
  (id, kind, semantics, date, precision, text digest, status, follow-up,
  `updated_at`).
- **Never:** the opinion deadline (a WAIT/MONITOR date is a review/expected
  date, said so in the preview), the stage, planned steps, completion.
- **Product note:** since ADR 0052 the web form no longer lets a person choose
  WAIT/MONITOR; the canonical service accepts them and the owner's MCP brief
  asks for them. Flagged to the lead.

### `complete_action` — the current step is done

- **Arguments:** `matter`, `action_id` (optional — the approval page names the
  step either way; get_matter does not expose step ids), `what_was_done`,
  `follow_up_outcome` `response_received`|`no_response`|`monitoring_ended`,
  `next_check_on`, `next_step_text`, `next_step_on` (exact day).
- **Ordinary step:** `what_was_done` required (`missing_information`; never a
  bare «done»); `follow_up_outcome` refused. Optional next step in the user's
  words. Service `workspace.complete_current_action` (note + completion + next
  step, or promotion of the earliest planned step, which the preview names).
- **Opinion follow-up check** (`follow_up_id` set): `follow_up_outcome`
  required; `no_response` needs `next_check_on` (not in the past);
  `monitoring_ended` needs the reason in `what_was_done`; no next step. Service
  `workspace.check_opinion_follow_up`.
- **State:** `current_action`, `first_planned_action`, owner.
- **Never:** a stage move or closure (the web form's `Uus hetkeseis` is not
  offered), a consultation round's feedback wait (that is not a `NextAction`),
  a planned step, the opinion deadline.

### `response_deadline` — «Arvamuse tähtaeg»

- **Arguments:** `matter`, `mode` `new_request`|`move`, `deadline`
  (`YYYY-MM-DD`), `current_request_outcome` (`answered`|`not_answering`|
  `superseded`; only `new_request` over a still-current request, and then
  required), `note` (required with `answered`; optional reason with `move`).
- **`new_request`:** no current deadline → `request_response_deadline` (fresh
  `response_requested_at`: an opinion sent earlier does not answer it). A
  pre-tracking deadline that already reads as answered → the same service,
  which writes it to history as answered with `LEGACY_DISCHARGE_NOTE` — the
  preview says so. A current outstanding request → `missing_information` until
  the user says what happened to it, then `change_response_deadline(change=REPLACED,
  previous_outcome, previous_note)`. A Koja algatus has no new request.
- **`move`:** `change_response_deadline(change=MOVED)` — same request, the old
  date in history as `MOVED`; needs a current deadline and a different date.
- **`correct`:** **not offered.** No service rewrites a recorded deadline as if it
  never was; a wrong date is moved (history kept) or ended in Juristid's header.
- **Stale guard twice:** state (`response_deadline`, `response_requested_at`,
  `deadline_revision`, `last_history_row`, `track`) and the service's own
  `expected_revision` from prepare.
- **Warnings:** deadline before `Saabus` (`DEADLINE_BEFORE_RECEIVED` — the web
  asks for a confirmation; here the approval is the confirmation), a past date.
- **Never:** marks an opinion sent, ends/clears a deadline, sets a step.

### `add_important_date` — «Oluline tähtaeg»

- **Arguments:** `matter`, `title` (≤ 2000), `date` + `date_precision`
  (`exact`|`month`|`quarter`|`year`), `kind` `other`|`transposition_deadline`|
  `planned_commencement`|`estimated_commencement`, `note`.
- **Service:** `intelligence.services.add_important_date` (its own
  `_check_bounds` precision validation under the open-Matter lock). The workspace
  door does not pass `kind`; the service does, and an estimated commencement must
  be stored as one.
- **Notifications:** none. Commencement reminders (ADR 0188) are generated only
  from `MatterEffectiveDate` («Jõustumine») and exclude planned/estimated
  commencements; no `CommencementReminder`, no `NextAction`, no `MatterEffectiveDate`
  is created (tested).
- **No save-once:** the service has no token; once-ness is the gateway's.

## Tables each `execute` writes

Observed by capturing SQL in `tests/test_mcp_write_operations_*.py`
(`commit` asserts each run is inside `WRITE_TABLES`). Every operation also
takes `SELECT … FOR NO KEY UPDATE` on `matters_matter`, which in PostgreSQL
needs `UPDATE` privilege on it.

| operation | INSERT | UPDATE | DELETE | row locks |
| --- | --- | --- | --- | --- |
| create_matter | matters_matter, matters_matterreferencesequence, matters_matterassignmentnotice, workflow_matterplanstep, audit_changeevent, search_searchdocument | matters_matterreferencesequence, search_searchdocument | search_searchdocument | matters_matterreferencesequence (FOR UPDATE), workflow_matterplanstep |
| update_matter | audit_changeevent, search_searchdocument | matters_matter, search_searchdocument | search_searchdocument | |
| assign_matter | matters_matterassignmentnotice, audit_changeevent, search_searchdocument | matters_matter, matters_matterassignmentnotice, workflow_nextaction, search_searchdocument | search_searchdocument | |
| add_matter_note | matters_entry, matters_mattersaveonce, audit_changeevent, search_searchdocument | search_searchdocument | search_searchdocument | |
| record_meeting | matters_entry, matters_mattersaveonce, audit_changeevent, search_searchdocument | search_searchdocument | search_searchdocument | |
| add_procedural_link | matters_matterprocedurallink, audit_changeevent | | | matters_matterprocedurallink |
| set_next_action | workflow_nextaction, audit_changeevent | workflow_nextaction | | workflow_nextaction |
| complete_action | matters_entry, workflow_nextaction, audit_changeevent, search_searchdocument | workflow_nextaction, workflow_opinionfollowup, search_searchdocument (+ workflow_matterplanstep when the step came from the plan) | search_searchdocument | workflow_nextaction, workflow_opinionfollowup |
| response_deadline | matters_matterresponsedeadline, audit_changeevent, search_searchdocument | matters_matter, search_searchdocument | search_searchdocument | |
| add_important_date | intelligence_matterimportantdate, audit_changeevent, search_searchdocument | search_searchdocument | search_searchdocument | |
