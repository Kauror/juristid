# Lawyer feedback 13 — what remains, and the two decisions it needs

**Status: AMBER.** Neither remaining subrequirement can be implemented without a
product decision that nobody has made. Both are recorded in the repository as
*deliberately open*, with reasons, and inventing an answer here would be this
agent making a product call under cover of a completion task.

Written against `origin/main` at `3503074` (18 September 2026).

---

## 1. What the lawyers actually asked, and what is already done

The single strongest source is **docs/adr/0091 §6.5**, «Lawyer feedback 13, item
by item» — a table the ADR author wrote precisely so that «nobody has to infer
which is which from the code». It records the request as naming **five things**:

| asked for | state on `main` | where |
| --- | --- | --- |
| the send date | **implemented** | `Saatmise kuupäev`, ADR 0091 §6.2 |
| the file itself | **implemented** | one upload, `KODA_SUBMISSION_FINAL`, §6.1 |
| the recipients | **implemented** | `Adressaadid`, `SubmissionRecipient`, §6.3 |
| a title for the opinion | **implemented** | `Submission.title`, §6.1 |
| **keywords on the opinion** | **deliberately not introduced** | §6.5 |
| **association to the Koda publication** | **deferred** | §6.5, §8 |

(The table lists six rows against «five things» because the title was answered by
a column that already existed.)

Beyond those, the Koda opinion is a canonical `Submission` and already supports
the sent date, immutable sent evidence, recipients, several opinions per Matter,
and withdrawal/supersession/history. That half is genuinely complete.

**Corroborating source:** `docs/adr/README.md` line 193 summarises the same round
as «per-opinion keywords deliberately not introduced and the publication
association deferred with no publication model added». Two independent places in
the repository say the same thing, in the same words, on purpose.

### Classification of the evidence

- **REQUIRED BY LAWYER FEEDBACK** — that an opinion can carry keywords of its
  own, and that a particular opinion can be tied to a particular `Ülevaade /
  uudis`. Both are named in the original request.
- **INFERRED PRODUCT DESIGN** — every question below about *shape*: which
  vocabulary, which cardinality, whether the relation is optional, where it
  surfaces. The feedback names the capability, not the model.
- **NICE TO HAVE** — searching and filtering opinions by their own keywords, and
  reporting over the opinion↔publication relation. Nothing in the source
  evidence asks for either; they are the obvious next step once the structure
  exists, which is not the same as being part of the request.

---

## 2. Current architecture

**`Submission`** (`app/submissions/models.py`) carries `matter`, `kind`, `title`,
`status`, `recipients` (through `SubmissionRecipient`), `joint_submitters`,
`sent_at` + `sent_at_precision`, `channel`, `reference`, `final_version`,
`working_document`, `notes`, and visibility inheritance. **It has no
classification of any kind** — no tags, no keywords, no free text intended as
one.

**`taxonomy.Tag`** is a *governed* vocabulary: `key`, `name_et`, `definition`,
`is_active`, `deprecated_at`, `merged_into` (a deprecated tag stays searchable
through its canonical successor), `owned_by`, plus `TagAlias` for name forms.

**`matters.TagAssignment`** binds a Tag to a Matter — and **only** to a Matter:
it is a concrete through-model with `matter = FK(Matter)`, `tag`, `source`
(`TagAssignmentSource`) and `confirmed_by`. Its docstring states the rule that
matters most here: *«A machine suggestion is not an assignment. Nothing writes
here until a person has accepted it.»*

**`MatterWebsiteOverview`** (ADR 0081/0085/0089) is the canonical publication
activity: `matter`, `status` (`PLANNED`/`PUBLISHED`/`CANCELLED`), `url`,
optional `published_on`. It is deliberately neutral — no `kind` column, because
an overview and a news item are the same act. A Matter may carry **none, one or
several**; there is no uniqueness on `matter`. There is create, publish, cancel
and correct, and **no deletion**.

**There is no relation between `Submission` and `MatterWebsiteOverview` in either
direction.** Verified by grep across both apps.

---

## 3. Keywords on an opinion — the decision that is missing

### What the repository already decided, and why

ADR 0091 §6.5 declines the feature with a substantive argument, not a shrug:

> A Matter already carries the classification the department searches by — policy
> areas, the legal instrument, the stage — and an opinion is a send *of* that
> Matter. Adding a second, narrower keyword vocabulary attached to individual
> Submissions would create two places a topic can be classified, two answers to
> «what is this about», and a reporting question with no correct answer […] If
> the department later wants to search opinions by their own terms, **the honest
> form of it is a decision about the existing taxonomy, not a private keyword
> field on a send.**

That last sentence is the deferral. It says the *shape* is a taxonomy decision
and names its owner implicitly — the people who govern the vocabulary.

### The three options, and what each costs

**Option A — governed `Tag`, through a new `SubmissionTagAssignment`.**
Reuses the vocabulary the department already governs, so merges, deprecation and
aliases keep working on opinion keywords for free, and a tag merge does not
strand them. Requires a new through-model, because `TagAssignment.matter` is a
concrete FK and cannot hold a Submission. Migration required.
*Cost:* the honest one. *Risk:* the «two places a topic is classified» objection
ADR 0091 raises is real and is not dissolved by sharing the vocabulary — it is
about which record answers «what is this about».

**Option B — free-text keywords on `Submission`.**
Cheapest to build, and worst to live with. It is a second, ungoverned vocabulary
with no merges, no deprecation, no aliases and no owner; it degrades into
near-duplicate spellings within a year, and every report over it has to
normalise text. It also contradicts the product's standing preference for
governed vocabularies over typed strings.
*Not recommended.*

**Option C — generic assignment (contenttypes) reusing `Tag`.**
One table for Matter and Submission alike. Loses FK integrity and makes the
authorization queries harder to reason about, on a product whose visibility
filtering must run before projection, ranking and counting.
*Not recommended here* for that reason alone.

### Recommendation

**Option A**, *if the owner decides the capability is wanted at all.* It is the
only option consistent with «the honest form of it is a decision about the
existing taxonomy».

### The default that must hold whichever option wins

**Matter tags must not be copied to a Submission.** If an opinion has its own
keywords they are facts about *that opinion*, not a second stored copy of the
Matter's classification. A copy would be a second thing that can disagree, and
correcting the Matter's tags would silently leave the opinion's stale. Any
inheritance wanted for *display* is a read-time join, never a write.

### Questions an owner must answer

1. **Is per-opinion classification wanted at all**, given that ADR 0091 argued it
   should not be — and, if so, what does it let somebody do that Matter-level
   classification does not?
2. **Governed `Tag` or a separate vocabulary?** If governed: may an opinion carry
   a tag its Matter does not, and is that a signal or a mistake?
3. **What happens on a tag merge or deprecation** for opinion assignments — the
   same rule as Matter assignments, or different?
4. **Is an opinion's keyword set correctable after the opinion is sent?** A
   `Submission` in `SENT` is otherwise near-immutable; classification is
   metadata about it rather than part of the act, which argues yes — but that is
   a call, not a deduction.
5. **Do opinion keywords appear in search and in reporting**, and if so, does an
   opinion keyword make its Matter match a search? (Answering «yes» creates the
   two-answers problem ADR 0091 names.)
6. **Historical import:** do the reconstructed archive submissions get keywords,
   or do they stay unclassified and visibly so?

---

## 4. The opinion ↔ `Ülevaade / uudis` relation — the second missing decision

### What the repository already decided

ADR 0091 §6.5:

> `MatterWebsiteOverview` (ADR 0085) is the existing publication activity;
> relating one to a *specific* `Submission` requires a relation that does not
> exist, and creating a publication record here would collide with the package
> that owns publication. **No publication model is added by this branch.** […]
> the association remains a documented hook rather than a half-built column.

So the deferral is explicit, and its reason — package ownership — has since
lapsed: Package B landed as #233 and `MatterWebsiteOverview` is now an upstream
fact. **The blocker that remains is cardinality, and it is a product question.**

### Cardinality is genuinely unknown

Neither model constrains it, and nothing in the source evidence settles it:

- A Matter may carry **several** Submissions (ADR 0091 §6.4 — VTK, draft,
  Riigikogu proceedings and revised text are four sends).
- A Matter may carry **several** overviews (the model's own «Zero, one or many»,
  no uniqueness on `matter`).
- So the relation is, in the general case, **many-to-many** — and whether the
  department wants that, or wants to assert at most one publication per opinion,
  is exactly the decision.

An overview must certainly be able to exist with **no** opinion: a plan is
recorded before anything is sent, and `PLANNED` carries neither URL nor date. An
opinion must certainly be able to exist with **no** overview: most sends are
never written up. Both directions are therefore optional, which is the one part
that can be derived rather than decided.

### The relation must be explicit, and the brief is right to insist

It must never be inferred from matching dates, from the URL, from title text, or
from the two records sharing a Matter. A long proceeding routinely carries
several opinions and several write-ups in the same month; any inference would
attach the wrong pair some of the time, silently, on the record that is supposed
to say what Koda published about what it sent.

### The options

**Option A — `ManyToManyField` with an explicit through-model**
(`SubmissionWebsiteOverview`: `submission`, `overview`, `linked_by`,
`linked_at`). Honest about the real cardinality, records who asserted the link
and when, and leaves room for the link to be corrected without touching either
record. Migration required.
**Recommended.**

**Option B — nullable FK `MatterWebsiteOverview.submission`.**
«This write-up is about that opinion.» Simplest, and defensible if the department
says a write-up is always about at most one opinion. Cannot express a write-up
covering two opinions, which a long proceeding plausibly produces.

**Option C — nullable FK `Submission.website_overview`.**
Inverts Option B and is worse: it asserts an opinion has at most one write-up,
which is the direction most likely to be false (a koda.ee overview and a press
item about the same letter).

### Questions an owner must answer

1. **Can one opinion be written up more than once?** (If yes, Option C is out.)
2. **Can one write-up cover more than one opinion?** (If yes, Option B is out and
   Option A is required.)
3. **Who asserts the link, and when** — at `+ Koja arvamus`, at `+ Ülevaade /
   uudis`, or from either side afterwards?
4. **Is the link correctable, and is unlinking allowed?** The surrounding records
   have no deletion; an incorrect link is presumably corrected rather than
   removed, but that needs saying.
5. **Visibility:** a `Submission` and an overview may each carry a stricter
   override than their Matter. May a reader who can see the overview learn that
   *some* opinion is linked to it when they may not see that opinion? The
   product's answer elsewhere is no — existence must not leak — and the link's
   reads must be filtered on both ends.

---

## 5. Where it would have to surface

Separating «must exist structurally» from «useful later»:

**Must exist for the feedback to be honestly closed**
- The relation is recordable and readable from at least one of the two records'
  detail surfaces.
- Keywords, if adopted, are settable and correctable where the opinion is
  managed.

**Useful, and not part of the request**
- Matter overview and `Teema käik` showing the pairing inline.
- The Arvamused page filtering by opinion keyword.
- Reporting over either.

---

## 6. Concrete plan, once the questions are answered

Assuming Option A on both:

1. **Migration** — `SubmissionTagAssignment` (`submission`, `tag` PROTECT,
   `source`, `confirmed_by`, unique on `(submission, tag)`) and
   `SubmissionWebsiteOverview` (`submission`, `overview`, `linked_by`,
   `linked_at`, unique on `(submission, overview)`). Both additive, no backfill,
   **no copying of Matter tags**.
2. **Services** — named use cases (`set_submission_tags`,
   `link_submission_to_overview`, `unlink_…` if the owner allows it), each with
   its own `ChangeEvent`, each refusing a cross-Matter pairing, each taking the
   established row locks.
3. **Visibility** — `visible_to` on both new querysets, filtered on *both* ends
   of the link, before any projection, ranking or counting.
4. **UI** — a control where an opinion is managed, and the pairing rendered where
   the opinion is read.
5. **Tests** — cardinality in both directions; a cross-Matter pairing refused;
   the link never inferred from date, URL, title or shared Matter; child
   visibility stricter than the Matter honoured on both ends; no silent tag
   inheritance; existing Submission evidence and send invariants untouched.

---

## 7. What was deliberately not done

No code was written for either subrequirement. Implementing them would have
meant choosing a vocabulary and a cardinality that the repository twice records
as open questions with named consequences — which is a product decision, and not
this agent's to make.
