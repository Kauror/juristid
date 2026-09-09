/* Teema page — design refinement preview. PREVIEW ONLY.
 *
 * Two things, and deliberately nothing else.
 *
 * 1. The one interaction the refinement adds: clicking the `Järgmiseks` row
 *    toggles the composer (`04-interaction-spec.md` I01). Every other
 *    interaction on the page is a native `<details>` and needs no script.
 *
 * 2. A write guard. This page renders the real Matter's real controls — the
 *    header's inline editors, `✓ Tehtud`, the composer's save — because seeing
 *    them is the point of a visual review. What the design does *not* define is
 *    what any of them does: `05-state-matrix.md` has no saving state, no
 *    validation state and no refusal state anywhere on it. So the preview does
 *    not write. A save that succeeded here would swap the production Matter
 *    view's own markup into the middle of the refined page, and the product
 *    owner would be looking at two designs at once.
 *
 * Scoped to the preview root. Loaded from one template, on one route that only
 * exists under DEBUG.
 */
(function () {
  "use strict";

  var root = document.querySelector("[data-matter-refinement-preview]");
  if (!root) {
    return;
  }

  /* I01 — the row is the composer's own toggle.
   *
   * "Anywhere on the row that is not a button, a link or a form", which is what
   * keeps `✓ Tehtud` and `Muuda` doing their own jobs. `closest` rather than a
   * tag test on the target, because the click usually lands on a span inside
   * the control. */
  var row = root.querySelector(".uxnext");
  var composer = root.querySelector("details.composer");
  if (row && composer) {
    row.addEventListener("click", function (event) {
      if (event.target.closest("button, a, form, input, select, textarea")) {
        return;
      }
      composer.open = !composer.open;
      if (composer.open) {
        var first = composer.querySelector("textarea, input");
        if (first) {
          first.focus();
        }
      }
    });
  }

  /* The write guard. Both halves of it: an ordinary form submission, and the
   * HTMX request every inline editor on this page is wired to make. */
  root.addEventListener(
    "submit",
    function (event) {
      event.preventDefault();
    },
    true
  );

  document.body.addEventListener("htmx:configRequest", function (event) {
    var origin = event.detail && event.detail.elt;
    if (origin && root.contains(origin)) {
      event.preventDefault();
    }
  });
})();
