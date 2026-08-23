# 0029 — Reference data is governed, additive, and never invented from source strings

- **Status:** Accepted — integrated into main after ADR 0027 (Kaasamine) and ADR 0028 (archive workspace)
- **Date:** 2026-08-23
- **Builds on** ADR 0012 (legacy register import), ADR 0017 (statistics and the metric catalogue), ADR 0022 (deployment, backup and recovery), ADR 0024 (test data classification), ADR 0025 (multiple Matter senders), ADR 0026 (source data enrichment).

## Context

Wave 2 is deployed. Production holds a real register, a real opinion archive and
a working product — and, on the day this was written:

| | |
| --- | --- |
| `Organisation` | 0 |
| `PolicyArea` | 0 |
| `Matter.policy_areas` relations | 0 |
| `MatterSourceOrganisation` relations | 0 |

Several shipped features are therefore structurally present and practically
inert. Multiple Matter senders (ADR 0025) have no senders to choose from.
Addressee selection has no addressees. Organisation search searches an empty
table. The `Matters by policy area` metric reports every Matter as
*Klassifitseerimata*. And the OneNote → `PolicyArea` enrichment (ADR 0026)
reports 27 sections, 24 filing locations and 864 accepted links, every location
`UNMAPPED` — not because the mapping engine is broken, but because there is
nothing on earth for it to map onto.

`manage.py deployment_readiness` nevertheless reported the deployment **ready**.
That is the blind spot: readiness checked that the schema, the mounts and the
authenticator agreed, and never asked whether the vocabulary the product runs on
existed at all.

Two things have to be established before any of that can be fixed, and they are
easy to conflate:

1. **What the reviewed vocabulary is** — a business question with a business
   source, not a modelling question.
2. **What should happen to fifteen years of raw counterparty strings** — a data
   question that cannot be answered until the first is settled and measured.

Answering the second before the first is how a reference-data change becomes a
silent rewrite of historic filing.

## Decision

### Reference data and synthetic seed are different things in the same tables

`seed_dev_data` invents `Näidisministeerium` and five provisional categories so a
developer has something to click. Those are props: wrong on purpose, so nobody
mistakes a rehearsal for the real register. Reference data is the opposite — the
actual public vocabulary, which belongs in a real deployment and would be
useless as an invention.

The rule that follows: **a fixture may use reference data, and may never create
it.** `seed_dev_data._seed_policy_areas` now reads the migration-seeded rows, the
same way `_seed_stages` has always read the stage vocabulary, and
`seed_e2e_data` does likewise. `PROVISIONAL_POLICY_AREAS` is kept as the record
of what a development database used to look like and is no longer written:
`maksundus` beside `maksud`, or `tooeigus` beside `toojoud`, would leave a
developer with two spellings of one concept and no way to tell which a report
counted.

### The policy-area vocabulary is Koda's own published list

Eesti Kaubandus-Tööstuskoda publishes what it works on under *Meie mõju ja
eesmärk* → *Millega tegeleme?* (https://www.koda.ee/et/meie-moju, read
2026-08-23). It names nine focus areas, and the baseline is those nine, in page
order:

| key | name | public heading, where it differs |
| --- | --- | --- |
| `maksud` | Maksud | |
| `toojoud` | Tööjõud | |
| `keskkond` | Keskkond | |
| `energeetika` | Energeetika | |
| `halduskoormus` | Halduskoormus | Võitlus halduskoormusega |
| `aus-konkurents` | Aus konkurents | Aus konkurentsikeskkond |
| `arioigus` | Äriõigus | |
| `riigihanked` | Riigihanked | |
| `haridus-ettevotlikkus` | Haridus ja ettevõtlikkus | Hariduse ja ettevõtlikkuse edendamine |

Three public headings are sentences rather than labels. The database carries the
concept and records the heading beside it, so the trail from business source to
row survives a reword without a checkbox list reading like marketing copy.

**The website is evidence, not a dependency.** Nothing at runtime reads koda.ee,
and nothing ever will. A page the communications team rewords must not silently
reclassify a decade of filing; changing this vocabulary is a code change with a
migration behind it. The URL and date exist so a reviewer can check the claim.

### `PolicyArea` stays small; `Tag` is where subjects go

Pension, käibemaks, AI, kestlikkusaruandlus, välistööjõud, ehitus, transport,
eksport, digitaliseerimine and a dozen other genuinely important topics are
deliberately absent. `PolicyArea` is the small stable reporting classification —
the axis a yearly report is cut along — and it stops being that the moment it
grows a row per subject. Narrower concepts are `Tag`, or Matters inside one of
the nine. A tenth area is a business decision, not a convenience.

**No `Tag` is seeded at all.** The authoritative tag vocabulary has not been
reviewed, and `EXAMPLE_TAGS` is illustrative. Production `Tag` stays at zero,
and that is the correct number.

**The nine are not a partition.** `Matter.policy_areas` is many-to-many because
an energy tax genuinely is both Maksud and Energeetika, and a reporting
obligation in an environmental permit is both Keskkond and Halduskoormus.

### The vocabulary arrives as a reviewed data migration

`taxonomy/0002_reference_policy_areas`, `RunPython` only, no schema change and no
`RunSQL`. A classification list assembled by hand in production is not
reviewable, and `Matter.policy_areas` points at these rows.

The migration carries a **frozen literal copy** of the manifest rather than
importing it. A historical migration that imported today's manifest would replay
as something different every time the manifest was edited. A test asserts the
two agree, which is what keeps the copy honest: the next vocabulary change is a
new manifest entry *and* a new migration, never an edit to this one.

It **fails closed**. A row already carrying one of these keys under a different
name, or one of these names under a different key, is somebody's decision. The
first would move every Matter filed under that area; the second would make every
name-based match ambiguous — including the OneNote section resolution, which
matches on exactly that. Both raise.

The reverse removes only rows this migration created that are still pristine and
that nothing points at. It does **not** delete a used area: that would cascade
through `Matter.policy_areas` and take the classification of every file with it,
which is far worse than a rollback leaving nine unused rows behind. Nor does it
raise when an area is in use — rolling code back while real filing exists is the
ordinary reason to run a reverse, and one that refuses exactly then is one
nobody can use. Retiring an area in future means `is_active = False`, never a
delete, so historic relations survive.

### The public organisation baseline is small and additive

The eleven current ministries (verified against valitsus.ee on 2026-08-23) plus
four bodies legal-policy work cannot be described without: **Riigikogu**,
**Vabariigi Valitsus**, **Euroopa Komisjon**, **Euroopa Parlament**.

That is the whole list, and its smallness is the decision. A complete Estonian
public-sector directory — boards, inspectorates, agencies, municipalities,
associations — is a different project with a different evidence requirement, and
seeding hundreds of unchecked rows would make the register look authoritative
while being unverified. What else belongs is a question for the coverage
measurement below, not for a guess today.

**Private organisations are not seeded.** Companies, industry associations and
member organisations arrive through ordinary application work or a later
reviewed source-specific import. Public baseline reference data and historical
source matching are different problems and are kept apart.

Two identity rules, inherited from `app.organisations.services` and now enforced
by tooling:

- **Never merge institutions because their names are similar.** Matching is
  normalised-exact plus reviewed aliases. No Levenshtein, no trigram, no
  embedding, no substring, no "looks like". `Keskkonnaministeerium` and
  `Kliimaministeerium` score highly against each other and have different
  remits.
- **Never rewrite a row that already exists.** `apply` creates a missing
  institution and adds a missing alias. It never renames, retypes, re-codes,
  sets validity dates, changes a predecessor, merges two rows, moves an alias
  between institutions, or deactivates anything. An additive mistake is
  recoverable; an overwrite is not.

**`EK` is deliberately not an alias for Euroopa Komisjon.** In Estonian it is
used for Euroopa Komisjon, Euroopa Kohus and Euroopa Kontrollikoda alike, and an
alias is an identity decision that matching then trusts absolutely — one wrong
`EK` files a Commission consultation under the Court. The Commission is seeded
without an abbreviation; a reviewed alias can be added later against real
register evidence. `EP`, `Valitsus` and the ministry abbreviations have no
competing referent in this domain and are kept.

**Validity dates and predecessors are left blank** unless an authoritative
reviewed source supports the relation. Blank is better than fabricated history.

### plan / apply / verify, and a digest between them

`manage.py reference_data plan` reads and decides, writes nothing, and ends with
a SHA-256 over exactly the changes it proposes — manifest versions included, so
a digest approved under one vocabulary cannot be spent under another.

`manage.py reference_data apply --expect-plan-sha256 <digest>` recomputes the
plan inside one transaction and refuses if it moved. Between an operator reading
a plan and running it, somebody may have created one of these institutions by
hand; applying a stale plan would produce the duplicate the plan existed to
prevent. It refuses outright on any conflict — a conflict is a question about
identity, and there is no partial application of a baseline nobody finished
reviewing.

`manage.py reference_data verify` answers the yes/no question and exits non-zero
on a broken baseline.

Policy areas are **read** by all of these and **written** by none. A second
write path for the same rows would mean two answers to "where did this
vocabulary come from", and the migration would stop being the record.

### Readiness now requires the baseline

`deployment_readiness` fails a `REAL_DATA_ALLOWED` deployment whose reviewed
vocabulary is absent, and says which domain is missing and what to run —
`migrate` for policy areas, `reference_data plan`/`apply` for institutions — not
a bare "deployment not ready".

Deliberately **not** a Django system check. `manage.py check` runs in every
isolated unit test and every developer's shell, where a hard requirement for
production reference data would fail thousands of tests that have no business
caring.

### Coverage is measured before any backfill is designed

`manage.py reference_data coverage --expect-register-snapshot-sha256 <sha>` is a
read-only diagnostic: how much of the register's counterparty column the
reviewed institutions would resolve, split by era and direction.

The era semantics are load-bearing. Column G is `KELLELT` through 2019 and
`KELLELE` from 2020 — who sent it, then who it was sent to. The direction is
read from the reviewed era contract, never inferred from the header text or the
year, and the two are never summed: a report that merged them would describe a
correspondence pattern that never existed.

A cell resolves as a whole or not at all. `Rahandusministeerium ja Justiits- ja
Digiministeerium` is `UNMATCHED`, not two matches — whether it means two
institutions or one institution and a copy recipient is a reading of the source
a person has to make, and splitting on `ja` would manufacture relationships out
of punctuation. That a Matter can now hold several senders (ADR 0025) is a fact
about the destination, not a licence.

Unresolved raw values are counted but never printed: they are register content,
and the unresolved ones are by definition the least standard spellings in the
file. `--output <path>` writes them for human review, and refuses any path
inside the checkout.

**No canonical Matter relationship is written by this work.** Not
`Matter.source_organisations`, not `Matter.addressee_organisation`, not a
`PolicyArea` link. Backfill policy and its audit semantics are decided *after*
the coverage numbers exist, which is the whole point of measuring first.

## Alternatives considered

**Seed the five provisional categories as production truth.** They are
explicitly synthetic and were never reviewed against anything. Two of them
(`ettevotluskeskkond`) do not correspond to a published Koda focus area at all.

**Derive policy areas from OneNote section names.** The sections are where a
lawyer filed something between 2011 and 2025 — a filing history, not a reporting
taxonomy, and full of `Muud` and `ARHIIV`. ADR 0026 already refused to treat one
as the other; this ADR is what finally gives that refusal something to map onto.

**Create `Organisation` rows from the register's raw counterparty strings.** It
would populate the table overnight and permanently. The strings contain
compounds, abbreviations, personal names, historical bodies and typos, and a row
created from one is indistinguishable afterwards from one a person reviewed.
Coverage measures the gap instead.

**Fuzzy-match institutions to raise the coverage number.** The number would go
up and the data would go wrong, silently and irreversibly, in exactly the cases
nobody re-reads.

**Scrape koda.ee periodically.** A marketing reword would reclassify a decade of
filing with no review and no migration.

**Put the requirement in a Django system check** rather than in readiness. It
would fail every isolated unit test in the repository.

## Consequences

- Production gains nine policy areas on `migrate`, and fifteen public
  institutions once an operator runs `reference_data apply`.
- `deployment_readiness` will refuse a real-data deployment between those two
  steps. That is intended: the deployment genuinely is not ready in between.
  The serial sequence is: back up → deploy → migrate → `reference_data plan` →
  read it → `apply` → `verify` → rerun the OneNote inventory/plan → rerun
  coverage.
- The `Matter` create form and the register filters now list nine real areas
  instead of two synthetic ones, which changes the affected visual baselines.
  That churn is the feature, not incidental drift.
- `REVIEWED_ALIAS_RULES` stays empty. The evidence for a reviewed OneNote alias
  does not exist until the real areas are deployed and the inventory is rerun.
- Nothing about the opinion archive, `JÄRGMISEKS`, the historical cutover or
  P4 changes.

## Reversibility

High for the tooling, deliberately partial for the data. The commands and the
readiness check can be removed without trace. The migration's reverse removes
only pristine, unreferenced rows — once a Matter is filed under an area, that
relation is real work and reversing a migration is not the way to discard it.
Retiring an area is `is_active = False`, which keeps every historic relation.
