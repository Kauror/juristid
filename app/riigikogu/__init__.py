"""The Riigikogu as a source: bills, documents, invitations, agendas, stenograms (ADR 0224–0228).

Everything here is an **external fact** read from the Riigikogu's open-data API
(api.riigikogu.ee, CC BY-SA 3.0). Nothing in this package is a fact about a Teema
until a person takes it there through the Teema's own services. Which Teema a bill
belongs to is not stored here either: that relationship is the register watch's
`WatchTarget` (source RIIGIKOGU, identifier = the bill's UUID), so there is one
register of Teema ↔ bill links, not two (ADR 0224 §2).
"""
