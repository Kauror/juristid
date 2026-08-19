"""Read-only OneNote extraction, kept outside the application.

Nothing in `app/` imports this package and nothing here imports Django. That
separation is the point: the notebook lives on a Koda-controlled machine, the
rehearsal server does not have it and must never fetch it, and the web
application has to start whether or not this tool is present
(Stage-2B brief 51).

The tool ends at a neutral archive plus a reconciliation report. It creates no
Matters, updates no register rows and imports nothing — the historical backfill
is a later, separately authorised migration (Stage-2B brief 62).
"""
