/* Minu asjad's own filter (templates/matters/partials/work_filter.html).
 *
 * Narrows the rows on the page as the reader types: a row stays when its
 * action, Teema and stage contain every word typed. Each band's count follows
 * and a band with nothing left is hidden; «Näita veel» is opened while a
 * filter is on so the rows behind it are searched too, and closed again
 * afterwards if it was closed. Esc or an empty field restores the page.
 * Nothing is sent anywhere. */
(function () {
  "use strict";

  function words(text) {
    return text.toLocaleLowerCase("et").split(/\s+/).filter(Boolean);
  }

  function setUp(input) {
    var page = input.closest(".page") || document;
    var bands = Array.prototype.slice.call(page.querySelectorAll(".workband"));
    var empty = page.querySelector("[data-workfilter-empty]");
    var label = input.closest(".workfilter");
    if (label) label.classList.add("is-ready");

    bands.forEach(function (band) {
      var count = band.querySelector(".workband__count");
      if (count) count.setAttribute("data-workfilter-total", count.textContent);
      var more = band.querySelector("details.pw-more");
      if (more) more.setAttribute("data-workfilter-open", more.open ? "1" : "0");
    });

    function apply() {
      var terms = words(input.value);
      var filtering = terms.length > 0;
      var shown = 0;
      bands.forEach(function (band) {
        var rows = band.querySelectorAll("[data-workrow]");
        if (!rows.length) return;
        var visible = 0;
        rows.forEach(function (row) {
          var parts = row.querySelectorAll(
            ".workrow2__action, .workrow2__subject, .workrow2__matter, .workrow2__stage"
          );
          var text = Array.prototype.map
            .call(parts, function (part) { return part.textContent; })
            .join(" ")
            .toLocaleLowerCase("et");
          var match = terms.every(function (term) { return text.indexOf(term) !== -1; });
          row.hidden = filtering && !match;
          if (!row.hidden) visible += 1;
        });
        var more = band.querySelector("details.pw-more");
        if (more) {
          more.open = filtering ? true : more.getAttribute("data-workfilter-open") === "1";
          more.classList.toggle("is-filtering", filtering);
        }
        var count = band.querySelector(".workband__count");
        if (count) {
          count.textContent = filtering ? String(visible) : count.getAttribute("data-workfilter-total");
        }
        band.hidden = filtering && visible === 0;
        band.classList.toggle("is-filtering", filtering);
        shown += visible;
      });
      if (empty) empty.hidden = !(filtering && shown === 0);
    }

    input.addEventListener("input", apply);
    input.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && input.value) {
        input.value = "";
        apply();
        event.preventDefault();
      }
    });
    // A value the browser restored on «back» is applied at once.
    if (input.value) apply();
  }

  function init() {
    document.querySelectorAll("[data-workfilter]").forEach(setUp);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
