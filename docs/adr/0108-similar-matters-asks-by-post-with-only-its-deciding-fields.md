# 0108 — `Sarnased teemad` asks by POST, with only the fields that decide it

**Status:** accepted
**Date:** 2026-09-24

**Amends ADR 0087 §4 and reverses one of its rejected alternatives.** 0087 §4 said
`GET /teemad/uus/sarnased/`, and 0087 rejected «posting the draft form to a
suggestion endpoint» because *GET states that it writes nothing, and the
parameters are short enough for a query string*. The second half of that was
wrong, and the first half never needed a GET to be true. Everything else in 0087
stands: one engine, no draft Matter, nothing written, nothing to press, and the
response replaces one region and names no control.

**No migrations. No search-index change.**

---

## 1. What the audit found (ENG-026, ENG-090)

- **The whole form went into the address.** The region asked with
  `hx-include="closest form"` on a GET, so every field on `Uus teema` went into
  the query string:
  - the private `Märkmed`;
  - the CSRF token;
  - the deadline, and everything else.

  An address is written down by every access log, proxy and browser history it
  passes through. The note is the one field on the form that is explicitly
  nobody else's.
- **The address could be too long.** A long `Lühikokkuvõte` made the request
  line longer than gunicorn accepts (4094 bytes).
- **Three of the five triggers were dead.** They listened
  `from:#id_policy_areas`, `from:#id_legal_instruments` and
  `from:#id_source_organisations`, and no element carries those ids. The chips
  are checkboxes named `policy_areas` and so on, each with its own id. Picking a
  Valdkond, an Õigusakt or a Saatja asked nothing, and the section kept
  answering the form as it was before the pick.

## 2. Decision

- **POST.** The route accepts only POST. A GET is a 405, so no client, and no
  link somebody pastes, can put the form in an address again.
  - The route still writes nothing. A test counts every statement it runs, not
    only the tables somebody thought of.
  - The token travels in the body, where a POST needs it.
- **An allow-list, not an inclusion.** htmx adds the enclosing form to a POST by
  itself, so the region names the only parameters that may leave the page:
  - `hx-params="title,brief_summary,policy_areas,legal_instruments,source_organisations,csrfmiddlewaretoken"`
  - `Märkmed` is not in the list, and neither is anything that does not decide
    the answer.
- **Triggers the page can actually fire.**
  - Title and summary keep their 600 ms debounce.
  - The three chip sets are heard once, at the form, filtered by the `name`
    they really carry. A 250 ms delay folds a burst of ticks into one request.
  - Hidden inputs written by the pickers do not match the filter, and neither
    does the note, so neither asks.
  - The `sarnased:restored` trigger of QA-11 is unchanged.

## 3. Consequences

- The access log shows `POST /teemad/uus/sarnased/` and nothing about the form.
- Picking a chip updates the section. It did not before.
- The route is no longer bookmarkable, which it never usefully was: it answers
  a half-filled form, not a page anybody navigates to.
