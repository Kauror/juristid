/* matter-actions.js: UI-09/UI-10 Matter action row and toolbar (agent E). Bound once, idempotently, and again after every HTMX swap, like app.js.
 *
 * `PRAEGUNE TEGEVUS` → the current step's toolbar: `✓ Tehtud | Vaatasin üle |
 * Muuda` (templates/matters/partials/current_action_task.html).
 *
 * Each button toggles only its own panel. The panels are drawn under the
 * step's line in a fixed order and are hidden rather than removed, so any
 * number may be open at once, the buttons never move, and whatever was typed in
 * a panel is still there when it is opened again. Opening and closing write
 * nothing.
 *
 * Everything here is delegated from `document`, so a swapped-in toolbar needs
 * no binding and a second load of this file would add nothing: the guard below
 * makes it a no-op.
 *
 * Across a swap of the workspace (somebody's save anywhere on the column), a
 * panel that was open is opened again when the step is still at the revision
 * it was drawn from (`data-curact-revision`). When the save changed the step,
 * the fresh panels arrive closed and drawn from the new revision, and anything
 * typed into the old ones is shown read-only as «Salvestamata sisu» by
 * app.js `keepDrafts` — the server would refuse it anyway
 * (`views._hold_the_drawn_revision`).
 */
(function () {
  "use strict";

  if (window.JuristidMatterActions) {
    return;
  }

  var TOGGLE = "[data-curact-toggle]";
  var PANELS = ".curact__toolpanels";
  /* Shut is a class the stylesheet hides only where scripting runs
     (matter-actions.css), so without scripting every panel is shown. */
  var SHUT = "curact__toolpanel--shut";

  function isShut(panel) {
    return panel.classList.contains(SHUT);
  }

  function toggleFor(id) {
    if (!id) {
      return null;
    }
    return document.querySelector(TOGGLE + '[aria-controls="' + id + '"]');
  }

  function panelOf(button) {
    return document.getElementById(button.getAttribute("aria-controls") || "");
  }

  function setOpen(button, open) {
    var panel = panelOf(button);
    if (!panel) {
      return false;
    }
    panel.classList.toggle(SHUT, !open);
    button.setAttribute("aria-expanded", open ? "true" : "false");
    return true;
  }

  /* The buttons say what their panels show — after a swap htmx may have kept
     an old panel (`hx-preserve`, a draft carried across) beside a new button. */
  function syncAll() {
    document.querySelectorAll(TOGGLE).forEach(function (button) {
      var panel = panelOf(button);
      if (panel) {
        button.setAttribute("aria-expanded", isShut(panel) ? "false" : "true");
      }
    });
  }

  window.JuristidMatterActions = {
    /* Open the toolbar panel with this id; false when the page has no such
       toolbar panel (then the caller's own way of opening applies). */
    open: function (id) {
      var button = toggleFor(id);
      return button ? setOpen(button, true) : false;
    },
    close: function (id) {
      var button = toggleFor(id);
      return button ? setOpen(button, false) : false;
    },
    isOpen: function (id) {
      var button = toggleFor(id);
      return !!button && button.getAttribute("aria-expanded") === "true";
    },
  };

  document.addEventListener("click", function (event) {
    var button = event.target.closest ? event.target.closest(TOGGLE) : null;
    if (!button) {
      return;
    }
    event.preventDefault();
    setOpen(button, button.getAttribute("aria-expanded") !== "true");
  });

  /* Escape inside an open toolbar panel closes that panel and gives the
     focus back to its button. Not when something inside already took the key
     — a calendar, a suggestion list (JUX-007). */
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape" || event.defaultPrevented || !event.target.closest) {
      return;
    }
    var panel = event.target.closest(".curact__toolpanel");
    var button = panel ? toggleFor(panel.id) : null;
    if (!button || isShut(panel)) {
      return;
    }
    event.preventDefault();
    setOpen(button, false);
    button.focus();
  });

  /* -- Across a swap ------------------------------------------------------- */

  /* Per request, keyed on its XMLHttpRequest: two swaps in flight at once
     (a save and an out-of-band refresh, say) each keep their own record, so
     neither can drop the other's reopen. */
  var before = new WeakMap();

  document.addEventListener("htmx:beforeSwap", function (event) {
    var target = event.detail && event.detail.target;
    var xhr = event.detail && event.detail.xhr;
    var holder = document.querySelector(PANELS);
    if (!xhr || !target || !holder || !target.contains(holder)) {
      return;
    }
    var requester = event.detail.requestConfig && event.detail.requestConfig.elt;
    var own = requester && requester.closest ? requester.closest(".curact__toolpanel") : null;
    var ok = event.detail.xhr && event.detail.xhr.status < 300;
    var open = [];
    holder.querySelectorAll(".curact__toolpanel").forEach(function (panel) {
      /* The panel whose own save succeeded stays closed: that save is done. */
      if (!isShut(panel) && !(ok && own === panel)) {
        open.push(panel.id);
      }
    });
    before.set(xhr, {
      revision: holder.getAttribute("data-curact-revision") || "",
      open: open,
    });
  });

  document.addEventListener("htmx:afterSettle", function (event) {
    var xhr = event.detail && event.detail.xhr;
    var done = xhr ? before.get(xhr) : null;
    if (done) {
      before.delete(xhr);
      var holder = document.querySelector(PANELS);
      if (holder && (holder.getAttribute("data-curact-revision") || "") === done.revision) {
        done.open.forEach(function (id) {
          var panel = document.getElementById(id);
          if (panel && holder.contains(panel)) {
            panel.classList.remove(SHUT);
          }
        });
      }
    }
    syncAll();
  });
})();
