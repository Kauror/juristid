"""In-app notifications: «Kontrolli jõustumist» and personal deadline notices.

Two things, deliberately narrow.

* **«Kontrolli jõustumist»** (docs/adr/0188). When a recorded commencement date
  arrives, the Matter's responsible lawyer is asked to *check* whether the act
  or provision actually takes effect as recorded. Nothing here decides that it
  did: a date passing is never evidence of entry into force, and no surface
  this app draws says «jõustus».
* **Personal deadline notifications** (docs/adr/0198). Advance, on-the-day and
  (for management) missed-deadline notices about the dated work the canonical
  selectors already define, with per-person preferences, in one inbox with
  «Uus asi» and the commencement checks — behind the bell, on Minu asjad and
  at `/teavitused/`.

In-app only. No push, and no e-mail: a wish to receive e-mail is stored and
refused until personal sign-in exists (`app.notifications.email`).
"""
