# 0227 — Full-document parliamentary analysis is built, controlled, and left off

Date: 2026-11-24 · Status: proposed — **the processing it describes is not authorised**; the code runs only against a mocked model (coordinator's brief, RK-3)

## Context

ADR 0217, 0218 and 0219 authorise a model to read public *metadata* — titles, dates,
senders — under `register_watch.outbound`. Reading whole documents (other organisations'
opinions, bill texts, koda's own published opinions) is a different processing operation,
even though every document is public. The brief asks for a controlled prototype and for
this decision to be prepared, not taken.

## Decision

1. **Off.** `JURISTID_RIIGIKOGU_DOCUMENT_AI` is false everywhere; the production adapter
   refuses to be built while it is (`analysis.AnthropicModel`), and the nightly ANALYSIS
   stage is recorded DISABLED. Turning it on requires the owner's separate approval of
   §2–§8 below.
2. **What may be sent, if approved.** Public Riigikogu documents of a followed bill: other
   organisations' opinions, bill texts and their explanatory memoranda, the lead
   committee's list of amendments, and koda's opinions *as published on the bill*. Never a
   Teema's notes, files, members' answers or anything of a restricted Teema; the only
   internal fact in a prompt is the instruction itself. A bill tied only to a restricted
   Teema is analysed only if the Teema's restriction is not about the bill's subject — an
   owner decision per case, because the request itself reveals interest (indirect
   disclosure). Personal contact details are stripped before sending. Provider processing
   and retention follow the provider's commercial terms; no training on inputs.
3. **One shared pipeline** (`riigikogu.analysis.run`) for every analysis type: positions,
   version changes, proposal outcomes, mention context. An `Analysis` row per source set
   (document UUIDs and content digests) and per schema and prompt version — an unchanged
   document is never analysed twice, a changed one supersedes only its own analysis.
   Input capped (120 000 characters), at most 3 attempts across nights, usage recorded.
   **Teiste seisukohad** is issue by issue: organisation, issue, provision, stance,
   argument, the exact passage and its document; its relation to koda's position on *that
   issue*: same, partly the same, different, additional argument, no stated position,
   insufficient evidence. Silence is never disagreement, and no position is inferred from
   an organisation's identity. A position that matters is recorded by a person through the
   existing `+ Kaasamine · Lisa tagasiside` workflow (ADR 0084, ADR 0091), not by the model.
4. **Version changes are established without a model** (`riigikogu.versions`). Only two
   documents whose role is the bill text are compared (not a memorandum, an annex, a signed
   container or the list of amendments); the texts are compared provision by provision,
   with renumbering recognised and formatting-only changes ignored, and the comparison says
   when it is unreliable. The model only explains changes the comparison found; an
   explanation for any other provision is dropped. Wording and interpretation are shown
   apart; nothing is presented as a legal conclusion.
5. **Official evidence first** (`riigikogu.evidence`). The lead committee's «Muudatusettepanekute
   loetelu» for the second reading states each proposal, its proposer and the committee's
   decision («ARVESTADA TÄIELIKULT», «ARVESTADA OSALISELT», «ARVESTADA SISULISELT», «JÄTTA
   ARVESTAMATA» — read on 892 SE on 24.11.2026). It is parsed deterministically. The
   Riigikogu Rules of Procedure provisions behind it (the brief cites RKKTS §§ 102–103)
   could not be read programmatically on 24.11.2026 and are to be verified before release.
6. **Work-victory candidates.** Evidence levels: DIRECT (the list names koda alone),
   SHARED (koda among others), ALIGNMENT (the later text matches koda's proposal, no
   document says why — the most a model may claim), INSUFFICIENT. The stage is kept (a
   second-reading text is not the adopted act). A person may press «Lisa töövõidu
   kandidaadiks», which goes through `add_work_victory_candidate`: a candidate, never a
   confirmed win; confirmation stays a person's act on the Teema.
7. **Untrusted text.** Sources are delimited and declared data in the system prompt; the
   model has no tools; only JSON is read; the answer is validated against the type's schema
   (a failure rejects the answer); every quoted passage must be found, word for word, in
   the document it names — an item that fails is moved to ``rejected`` and never shown.
   A late answer writes only its own row and cannot overwrite a person's decision.
8. **Labelled.** Every analysis says it is a model's reading; a mocked one says «Näidis:
   mudeli vastus on simuleeritud». Expected cost, if approved, is in the programme's
   delivery report.

## Consequences

- The prototype is reviewable on fictional data (`manage.py seed_riigikogu_demo`) without
  any external processing.
- Approval of this ADR is a precondition for any live analysis.
