# 0226 — Protocols, stenograms and answers to invitations are evidence, never verdicts

Date: 2026-11-24 · Status: proposed (coordinator's brief, RK-4 and RK-5; development authorisation only)

## Context

Committee protocols and plenary stenograms record who took part and what was said about
koda; invitations have deadlines that pass. Each is easy to over-read: an invitation is not
attendance, a mention is not support, a missing public opinion is not a missed deadline.

## Decision

1. **Read nightly, deterministically**, in the cycle of ADR 0222 §5 (PROTOCOLS, STENOGRAMS,
   RESPONSES). No model decides any of it.
2. **Stenograms.** The day's verbatims are searched for koda's names, including spoken
   forms («kaubanduskoja», «Kaubandus-tööstuskoja»), never the Põllumajandus-Kaubanduskoda
   or a bilateral chamber («Ameerika Kaubanduskoda»). Each mention is kept once (speech
   UUID and position) with the exact quote, its paragraph, the speaker as the stenogram's
   own heading writes it, the sitting and the agenda item. Under an agenda item about a
   bill it is «Õigusloomega seotud» and is shown on that bill's Teema; otherwise it is «Muu
   koja mainimine», shown read-only and folded on Monitooring → Riigikogu, never a Teema.
   No sentiment score. Who else may see the non-legislative mentions is to be reviewed
   before activation.
3. **Protocols.** The minutes of past sittings (``protocolDocuments``, last 21 days) are
   read once each. A passage naming koda inside the attendance block («Kutsutud:»,
   «Osalesid:») marks *participation*; elsewhere it is a mention. Invited, participated,
   mentioned and submitted an opinion are kept apart: an invitation alone never implies
   attendance.
4. **Answers to invitations.** Three days after an invitation's date has passed, the bill's
   opinion list is read once; an opinion titled as koda's settles it («Koja arvamus
   registris»). Otherwise the invitation reads «Kutsele vastamine vajab kontrollimist» —
   never «koda ei vastanud». A person may record: linked to koda's opinion, a joint
   letter, participation at the sitting, a decision not to answer, another activity — or
   leave it open. None of it enters any person's statistics, and no response rate is
   computed until classifications and coverage are validated.
5. **Deferred** (not built): a historical backfill into the live queues (the 2009–2026
   documents are not all invitations, and early metadata is incomplete — a backfill, if
   wanted, goes to a separate read-only report); permanent profiles of members of the
   Riigikogu, political scoring or predictions; automatic assignment of Teemad from EuroVoc
   descriptors; voting dashboards; EU monitoring (a separate programme); integration with
   the Riigikogu Chancellery's own assistant (no API verified; no outreach made).

## Consequences

- The evidence a work-victory candidate needs (ADR 0225 §6) and the evidence that koda
  took part are collected without anyone being judged by an absence.
