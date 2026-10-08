/*
 * The colour theme: dark, unless this browser has chosen light
 * (docs/adr/0147).
 *
 * Loaded in <head>, before the stylesheets and without `defer`, so it runs
 * before the browser has parsed <body> — before anything can be painted. A
 * browser that chose light therefore never shows a dark first frame. It is a
 * file rather than an inline <script> because `script-src 'self'` refuses
 * inline code (app/core/browser_policy.py), and it is its own file rather than
 * part of app.js because app.js is deferred, which is exactly too late.
 *
 * What it keeps is one key, `juristid-theme`, holding `dark` or `light` and
 * nothing else. It is a preference of this browser, not of a person: behind
 * the shared gate the person on the bar is a view somebody picked, so no
 * account, cookie or server ever hears of it. Signing out clears the origin's
 * storage (`Clear-Site-Data`, ENG-009), and the preference goes with it — the
 * page after signing out is dark, and the switch is on it.
 *
 * Every state the page shows lives on <html data-theme>. The switch's icon and
 * its spoken name are chosen by the stylesheet from that one attribute, so
 * nothing in <body> has to be kept in step, and no fragment an htmx swap
 * brings in can disagree with it. The click is caught on `document` for the
 * same reason: there is no element to rebind.
 */
(function () {
  "use strict";

  var KEY = "juristid-theme";
  var root = document.documentElement;

  /* Anything else in the key — an older spelling, a hand edit — is ignored,
     and the page keeps the dark default the server rendered. */
  function saved() {
    try {
      var value = window.localStorage.getItem(KEY);
      return value === "dark" || value === "light" ? value : null;
    } catch (error) {
      /* Storage is blocked. The page is dark, and the switch still works for
         as long as it stays open. */
      return null;
    }
  }

  function apply(theme) {
    root.setAttribute("data-theme", theme);
  }

  var initial = saved();
  if (initial) {
    apply(initial);
  }

  /* The switch does nothing without this script, so it is shown only when the
     script has run (static/css/app.css, `.themetoggle`). Set here, before
     <body>, so showing it never moves anything after the first paint. */
  root.setAttribute("data-theme-switchable", "");

  document.addEventListener("click", function (event) {
    var target = event.target;
    var toggle = target && target.closest ? target.closest("[data-theme-toggle]") : null;
    if (!toggle) {
      return;
    }
    var next = root.getAttribute("data-theme") === "light" ? "dark" : "light";
    apply(next);
    try {
      window.localStorage.setItem(KEY, next);
    } catch (error) {
      /* Switched, not kept. */
    }
  });

  /* Another tab switched, or cleared this origin's storage. */
  window.addEventListener("storage", function (event) {
    if (event.key === KEY || event.key === null) {
      apply(saved() || "dark");
    }
  });

  /* A page the browser brings back from its back-forward cache kept the theme
     it had when it was left, which may not be the one chosen since. */
  window.addEventListener("pageshow", function (event) {
    var theme = event.persisted ? saved() : null;
    if (theme) {
      apply(theme);
    }
  });
})();
