/*
 * «Vali veerud» on Teemad (app/matters/register_columns.py, Contract B).
 *
 * The chooser is a plain GET form: ticking boxes changes nothing until
 * «Rakenda» is pressed, so the applied table and the draft choice stay
 * separate. This script only keeps the preview line under the boxes in step
 * with what is ticked — «Tabelis: Pealkiri · Hetkeseis · …» — so a reader sees
 * the columns they are about to get. Without it the line shows the columns
 * the table has now, and the form still works.
 *
 * Delegated from the document, because the chooser sits in the results
 * region the live search replaces.
 */
(function () {
  "use strict";

  function preview(chooser) {
    var line = chooser.querySelector("[data-column-preview]");
    if (!line) {
      return;
    }
    var words = ["Pealkiri"];
    chooser.querySelectorAll("input[name='veerud']").forEach(function (box) {
      if (box.checked) {
        words.push(box.getAttribute("data-heading"));
      }
    });
    line.textContent = "Tabelis: " + words.join(" · ");
  }

  document.addEventListener("change", function (event) {
    var target = event.target;
    if (!(target instanceof HTMLInputElement) || target.name !== "veerud") {
      return;
    }
    var chooser = target.closest("[data-column-chooser]");
    if (chooser) {
      preview(chooser);
    }
  });
})();
