# 0225 — Riigikogu invitations to koda are read nightly, and their dates stay external facts

Date: 2026-11-24 · Status: proposed (coordinator's brief, RK-1; development authorisation only)

## Context

A Riigikogu committee asks for koda's opinion by e-mail and files the letter in its public
document register with its addressees. On 24.11.2026 a search of the register for
«Tööstuskoda» found 372 documents since 2009. Read one by one, 314 were letters the
Riigikogu sent with koda among the addressees, 51 were koda's own letters, and 7 named
koda only in passing or not as a party. They were not 372 invitations. Lovable owns the Teema's
`Arvamuse tähtaeg` and `Järgmiseks` (ADR 0213), and an external date must not become
anybody's work by itself.

## Decision

1. **koda is an addressee only by the metadata.** The register search finds candidates;
   `organisation.koda_role` decides from the direction and the ``author`` field (the
   addressee list of a letter the Riigikogu sends). Recognised: «Eesti
   Kaubandus-Tööstuskoda», «Eesti Kaubandus- ja Tööstuskoda», «Eesti Kaubandus-
   Tööstuskoda», en dash, any case form. Never the Eesti Põllumajandus-Kaubanduskoda, never
   a bilateral chamber. A document that only mentions koda is MENTIONED, not an invitation.
2. **What a letter to koda is** (`organisation.classify`): an invitation when its title
   says so («Kaasamiskiri …») or its own sentences ask («palun esitage», «Teil on võimalus
   avaldada arvamust», «kui Teil on ettepanekuid»); a NOTIFICATION when it only informs
   («Teavitatud huvirühmad: …»); otherwise another letter to koda (a reply to koda's appeal).
   koda's own registered letters and the opinions in a bill's list titled as koda's are
   KODA_OPINION. On the 24.11.2026 corpus of 372: 214 invitations, 49 notifications, 51
   other letters to koda (mostly replies to koda's appeals), 51 koda letters, 7 not about
   koda as a party. A random sample of 30 invitations held no false one; reading the letters
   left outside the class found four requests the phrases missed, now covered.
3. **External dates and their meaning.** An invitation's dates are `ExternalDeadline`s of
   kind OPINION_REQUEST and ATTENDANCE; the Riigikogu's amendment deadline is AMENDMENTS
   (ADR 0226 §3). None is koda's obligation. A person may take one onto the Teema as an
   `Oluline tähtaeg` of a new kind — RK_OPINION_REQUEST, RK_AMENDMENTS_DEADLINE,
   RK_COMMITTEE_SITTING — all three in `INFORMATIONAL_IMPORTANT_DATE_KINDS`: never the next
   step, never late work, never counted as workload or in consultation statistics, never a
   closure hold (important dates are not live work, `matters.closure_work`), and never
   `Arvamuse tähtaeg`. The kind, not the title, decides.
4. **The text of a letter is read and dropped.** The file (Word-generated HTML in
   Windows-1252, or PDF/Word) goes through the application's own parsers; only its digest
   and the sentences that carry a date are kept. The letter date and the registration date
   are kept apart: a letter can be registered, and so become readable, days later.
5. **Deadlines are read deterministically** (`deadlines.extract`). A date counts only in a
   clause that asks for something and marks a limit; the initiation date, a past sitting,
   the Riigikogu's own amendment deadline mentioned in passing and the committee's own
   proposal are not koda's deadline. Different actions keep different dates; a sitting is a
   hearing, not a deadline. A year is inferred from the letter's date only when that gives a
   day 0–120 days after it; a weekday is never made a date; a time is kept only when
   written. The states are «Tähtaeg tuvastatud», «Tähtaeg vajab kontrollimist» (with the
   reason), «Tähtaega ei leitud» and «Dokumenti ei õnnestunud lugeda»; the invitation is
   kept in every one of them, with the source sentence beside the date. **Measured** on the
   evaluation set (`tests/fixtures/riigikogu/deadline_cases.json`: 205 real letters'
   date sentences, sanitised and reviewed by hand, and 25 synthetic cases): 0 wrongly
   accepted dates; 195 of 195 unambiguous opinion dates read; 10 of 10 doubtful letters sent
   for checking; amendment deadlines 24 of 25 read, 0 wrong. CI holds the bar: no accepted
   wrong date, at least 95 % coverage overall and on the held-out years.
6. **The review workflow writes no Lovable field.** On the Teema's «Registritest» or on
   Monitooring → Riigikogu a person may: «Lisa tähtajana» (only a verified date; an
   informational date, §3); «Seo olemasoleva tähtajaga»; «Lisa meeldetuletus» — the
   person's own `PersonalReminder`, which changes nothing about the Teema's work, because
   `Järgmiseks` is Lovable's; «Edasist tööd pole vaja»; «Kontrolli tähtaega» (a person
   states the date the sentence gives; the doubtful row is superseded, both kept). The
   invitation is compared with the Teema's own `Arvamuse tähtaeg`: same, different (shown,
   never resolved), or none. An invitation on a bill no Teema follows enriches that bill's
   «Uued eelnõud» candidate, or makes one, marked «Riigikogu kutsus koda arvamust
   avaldama», and is always «worth telling» whatever the model says; no Teema is created or
   reopened by it.
7. **Notices.** A new invitation, or a material change of its date, tells the Teema's owner
   once (`RegisterNotice.invitations`, added to the Teema's unread notice) or, with no
   followed Teema or no active owner, the department head and the Monitooring audience
   (`CandidateNotice.invitations`, a count that names no Teema). An unchanged rediscovery
   tells nobody. No e-mail or other channel.

## Consequences

- The register is a second line of defence beside e-mail, and every surface says so.
- `Arvamuse tähtaeg` and `Järgmiseks` remain Lovable's; the Riigikogu's dates never compete.
- The evaluation set's truth was reviewed from the reader's own output: an independent
  relabelling of a sample is recommended before the deadline state «tuvastatud» is relied
  on without reading the sentence.
