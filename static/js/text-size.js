/*
 * The text size: Tavaline, unless this browser has chosen Väike, Suur or
 * Väga suur (docs/adr/0221, «Personal preferences»).
 *
 * The theme's twin (static/js/theme.js, docs/adr/0147), for the same reasons:
 * loaded in <head> without `defer`, so `data-text-size` is on <html> before
 * <body> is parsed and no frame is painted at the wrong size; a file because
 * `script-src 'self'` refuses inline code. One key, `juristid-text-size`,
 * holding `vaike`, `suur` or `vaga-suur`; Tavaline is the absence of the key
 * and of the attribute, so the default page is exactly what it was. A choice
 * of this browser, not of a person — no server hears of it, and signing out
 * clears it with the rest of the origin's storage (ENG-009).
 *
 * The sizes themselves are typography tokens redefined per value in
 * static/css/tokens.css. The buttons in Haldus → Kuvamine carry
 * `data-text-size-choice`; the click is caught on `document`, and each
 * button's `aria-pressed` is read from the one attribute on <html>.
 */
(function () {
  "use strict";

  var KEY = "juristid-text-size";
  var SIZES = ["vaike", "suur", "vaga-suur"];
  var root = document.documentElement;

  function saved() {
    try {
      var value = window.localStorage.getItem(KEY);
      return SIZES.indexOf(value) >= 0 ? value : null;
    } catch (error) {
      /* Storage is blocked: Tavaline, and the choice lasts while the page is open. */
      return null;
    }
  }

  function apply(value) {
    if (value) {
      root.setAttribute("data-text-size", value);
    } else {
      root.removeAttribute("data-text-size");
    }
  }

  function current() {
    return root.getAttribute("data-text-size") || "tavaline";
  }

  function mark() {
    var buttons = document.querySelectorAll("[data-text-size-choice]");
    for (var i = 0; i < buttons.length; i += 1) {
      var pressed = buttons[i].getAttribute("data-text-size-choice") === current();
      buttons[i].setAttribute("aria-pressed", pressed ? "true" : "false");
    }
  }

  function choose(value) {
    var chosen = SIZES.indexOf(value) >= 0 ? value : null;
    apply(chosen);
    try {
      if (chosen) {
        window.localStorage.setItem(KEY, chosen);
      } else {
        window.localStorage.removeItem(KEY);
      }
    } catch (error) {
      /* Not remembered; the page still shows the choice. */
    }
    mark();
  }

  apply(saved());

  document.addEventListener("click", function (event) {
    var target = event.target;
    var button = target && target.closest ? target.closest("[data-text-size-choice]") : null;
    if (!button) {
      return;
    }
    event.preventDefault();
    choose(button.getAttribute("data-text-size-choice"));
  });

  document.addEventListener("DOMContentLoaded", mark);
})();
