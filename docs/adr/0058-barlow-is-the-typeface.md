# ADR 0058 — Barlow is the typeface, not a fallback for one

- Status: accepted
- Date: 2026-09-02
- Related: ADR 0009 (the design token foundation, whose **Typeface** paragraph
  this supersedes), `docs/open-decisions.md` (which listed the web-font question
  as outstanding and no longer does)

## Context

ADR 0009 recorded the typeface as a licence problem: *"The CVI face is FF DIN Pro
and its web licence has not been delivered. Barlow is the approved visual
fallback and ships first in the stack after it, so adding FF DIN Pro later is an
`@font-face` addition, not a redesign."*

That framing has been the stack ever since:

```css
--typography-font-sans: "FF DIN Pro", Barlow, Arial, sans-serif;
```

and it has one property nobody chose. The application ships **no** `@font-face`
for FF DIN Pro, so a browser can only use it if the machine already has it
installed locally. Most do not, and get Barlow. Some do — the Chamber's own
designers, and anyone with the desktop licence — and get FF DIN Pro. The
department therefore sees two different renderings of the same page depending on
whose laptop it is on, and neither is wrong, and nothing says which one the
product is.

That is a worse position than either alternative. A design that is only correct
on the machines of the people who reviewed it is a design nobody can check.

## Decision

**Barlow is the typeface.** FF DIN Pro leaves the stack.

```css
--typography-font-sans: Barlow, Arial, sans-serif;
```

Not because the licence is unavailable — it is available; the Chamber holds FF
DIN Pro — but because Barlow is the better answer for a web application the
department uses all day:

* **It is already self-hosted and complete**, latin and latin-ext, four weights,
  ~200 KB, served from our own origin (ADR 0009's other typography decision,
  which stands: a font CDN would disclose who is using this and when, on every
  page view, for an application holding confidential member material).
* **Every render is the same render.** No machine-dependent substitution, so a
  screenshot in a bug report is the page the reporter saw, and the 35 visual
  baselines mean what they claim.
* **Estonian is covered.** `õ ä ö ü` and the latin-ext range are in the shipped
  subsets, which is not something to assume of a desktop face's web cut.
* **Nothing has to be licensed, embedded, subsetted or re-hosted later.** The
  `@font-face` addition ADR 0009 was holding the door open for does not happen,
  and the door closes.

This is a deliberate departure from the CVI's *typeface*, not from the CVI. The
colours remain CVI-mapped and unchanged: Chamber blue `#009FDA` and the
graphite-derived dark ramp, the ramp already validated for colour-blind
legibility. Barlow was chosen as the CVI's own approved visual fallback, so this
is adopting a decision Communications already sanctioned rather than inventing
one.

## Consequences

**Nothing renders differently in CI, and no baseline moves.** The CI runner has
no FF DIN Pro installed, so it has been rendering Barlow all along — which is
exactly why this drift was invisible. The pages that change are the ones on
machines that hold the desktop licence, and they change *towards* what everybody
else has been seeing.

**`docs/open-decisions.md` loses a row rather than narrowing one.** The
outstanding item was the FF DIN Pro web licence; there is now nothing to wait
for. Communications/CVI is not being asked for a font package.

**Reversible in one line**, and the reversal is a real decision rather than a
default: putting `"FF DIN Pro"` back in front of Barlow requires an `@font-face`
and the licensed web cut, at which point somebody has decided the desktop face
is worth the bytes and the licence administration. What this ADR removes is the
*undecided* state where the answer depended on the reader's machine.

**ADR 0009's typeface paragraph is superseded.** Its two other typography
decisions — self-hosting rather than a CDN, and the token architecture the face
resolves through — are untouched and still govern.
