"""OneNote ingress, Phase 1: read-only discovery and proof contracts.

This package is a prototype, not an importer. It parses the OneNote locators
Lovable carries, builds a source inventory, resolves each register topic to an
explicit state, verifies Graph page and resource responses against the identity
they were requested for, and states the identity, version and scan contracts a
Phase 2 scanner would have to honour.

Nothing here imports Django, writes a Matter, a Document or a source reference,
or sends anything to Microsoft other than GET requests built by
:mod:`tools.onenote_ingress.transport` against an explicit site scope. Real
notebook data never enters this repository: the tests build invented
notebooks at run time, and real runs write only to private storage outside Git
(docs/onenote-ingress-phase1.md).
"""
