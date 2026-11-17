# 0215 — Statistika reads who sends and Valdkond from confirmed Teema fields

Date: 2026-11-17 · Status: accepted (owner, 2026-11-17)

## Context

Phase 3 of Statistika (after ADR 0214) asks two questions of Koda's opinions:
who sent the request, and which Valdkond it belongs to. Juristid already has
the fields: `Matter.source_organisations` (KELLELT, 0..N) and
`Matter.policy_areas` (the governed Valdkond vocabulary). On production on
2026-11-17 they were empty on every one of the 2,370 live real Teemad. Lovable
never held either fact, and only 23 imported opinions name an addressee.

## Decision

1. **Statistika counts only the Teema's own fields.** «Valdkonna järgi» and
   «Saatja järgi» group the period's opinions like «Õigusakti liigi järgi»
   (`opinion_stats.by_group`); Teemad with no value form one «Märkimata» row.
   Nothing depends on Lovable.
2. **The fields are filled by proposal and confirmation.** The titles were
   reviewed once, in a Claude Code session, with no API and no program
   inside the application; the result is a reviewed file
   (`classification-2026-11-17.json`) loaded by
   `manage.py load_classification_suggestions`. Each Teema gets one
   `reporting.ClassificationSuggestion`. The proposal is not a figure and no
   statistic reads it.
3. **A lawyer confirms on «Saatja ja valdkond»** (`/statistika/liigitamine/`):
   per row as proposed or corrected, or the whole page as proposed. A
   confirmation *adds* to what the Teema holds through `set_policy_areas` and
   `set_organisations`, so it is audited exactly like the Teema page; a value
   somebody set by hand is never removed. «Jäta vahele» records the decision
   and changes nothing. A second load never reopens a decided row.
4. Confirming is a business write (`business_write_required`); a reader sees
   the proposals without the buttons. A proposal is as visible as its Teema.
5. **Confirming works on a closed Teema.** Filing a past Teema under its
   sender and Valdkond describes the record and adds no new content (the
   correction rule of ADR 0075 §12 / 0076 §2); without it the history from
   2011, almost all closed, could never be classified.

## Consequences

- The panels start at «Märkimata» for everything and fill as proposals are
  confirmed; the panel head links to the open proposals while any remain.
- Ministry names follow the 2023 and 2025 reforms; a Teema up to 2024 from the
  justice ministry is proposed as «Justiitsministeerium».
- New Teemad get no proposal; their sender and Valdkond are set on the Teema
  page as before.
