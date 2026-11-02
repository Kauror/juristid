"""«Majanduspoliitika ülevaade» — a Word report composed from recorded events.

A department lawyer writes a monthly overview of what the Chamber did: which
opinions and answers went out, which meetings and hearings Koda took part in,
what it said in public. This package proposes those events for a chosen period,
lets the reader choose, order and annotate them, and writes the result as a
DOCX (docs/adr/0199).

Three modules, one direction of dependency:

* :mod:`.sources` — **what counts.** The owner's source rules (Decision B),
  read as the requesting user through each model's own ``visible_to``. The
  only place an event becomes eligible.
* :mod:`.draft` — **what the reader chose.** The transient `ReportDraft`
  parsed from the posted form, validated, merged with the sources and resolved
  into one `ComposedReport` that both the preview and the DOCX render.
* :mod:`.docx` — **how it is written.** python-docx, nothing fetched, nothing
  embedded that is not text.

No draft is stored anywhere: not in the database, not in the session, not in
browser storage. The page's form fields are the draft, and every request
re-resolves every fact from canonical identifiers (docs/adr/0199 §3).
"""
