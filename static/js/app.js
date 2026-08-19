/*
 * Two small behaviours. Everything else is server-rendered HTML and HTMX.
 *
 * The rule this file follows: nothing here is the only way to do anything.
 * Every shortcut has a visible control, and the page works with JavaScript
 * disabled (master specification 17.7).
 */
(function () {
  "use strict";

  /* ---- Ctrl/Cmd+K focuses the global search ------------------------------
   * Focus, not a command palette. The search field is already visible on
   * every page; the shortcut saves a reach for the mouse and nothing more.
   */
  document.addEventListener("keydown", function (event) {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      var field = document.getElementById("global-search");
      if (field) {
        event.preventDefault();
        field.focus();
        field.select();
      }
    }
  });

  /* ---- Composer: Ctrl/Cmd+Enter submits ---------------------------------- */
  document.addEventListener("keydown", function (event) {
    if (!(event.ctrlKey || event.metaKey) || event.key !== "Enter") {
      return;
    }
    var composer = event.target.closest("form[data-composer]");
    if (composer) {
      event.preventDefault();
      composer.requestSubmit();
    }
  });

  /* ---- Disclosure toggles ------------------------------------------------
   * A plain <details> element would do most of this, but the composer needs
   * the checkbox state to reach the server, so the panel is driven by the
   * checkbox that is already being submitted.
   */
  function syncToggle(toggle) {
    var target = document.getElementById(toggle.getAttribute("data-toggles"));
    if (target) {
      target.hidden = !toggle.checked;
    }
  }

  function bindToggles(root) {
    (root || document).querySelectorAll("[data-toggles]").forEach(function (toggle) {
      syncToggle(toggle);
      toggle.addEventListener("change", function () {
        syncToggle(toggle);
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    bindToggles(document);
  });

  /* A rejected save returns 400 with the surface re-rendered and the errors in
   * place. HTMX drops non-2xx responses unless told otherwise, which would make
   * a validation failure look like nothing happened at all. */
  document.body.addEventListener("htmx:beforeSwap", function (event) {
    if (event.detail.xhr && event.detail.xhr.status === 400) {
      event.detail.shouldSwap = true;
      event.detail.isError = false;
    }
  });

  /* HTMX replaces whole surfaces, so re-bind inside whatever just arrived. */
  document.body.addEventListener("htmx:afterSwap", function (event) {
    bindToggles(event.target);
    var focusTarget = event.target.querySelector("[data-autofocus]");
    if (focusTarget) {
      focusTarget.focus();
    }
  });
})();
