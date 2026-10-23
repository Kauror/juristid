"""Haldus: one administration area for technical administrators and department heads.

A thin shell and nothing more (docs/adr/0186): a landing page, a section
registry (`sections.py`) and two small pages of its own — Osakonna töökorraldus
(links to the existing Osakond surfaces) and Süsteemi seis (read-only health
for a verified technical administrator). Account administration stays in
`app.accounts` and reference data in `app.reference_data`; each route enforces
its own authorization whatever the navigation shows.
"""
