/* Statistika's panel downloads as PNG (UX-005, templates/reporting/_chart_download.html).
 *
 * The server draws every panel as SVG (`reporting:chart_svg`); a PNG is that
 * same SVG painted on a canvas at twice its size and saved. The image is loaded
 * as an ordinary same-origin <img>, so the page's Content-Security-Policy
 * (img-src 'self') holds and the canvas is never tainted. «Läbipaistev» asks the
 * server for the SVG without its white background (`taust=puudub`), so the PNG's
 * empty pixels really are transparent. No library, nothing sent anywhere else.
 * Without script the menu still offers SVG and Excel. */
(function () {
  "use strict";

  var SCALE = 2;

  function save(blob, name) {
    var url = window.URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(function () {
      window.URL.revokeObjectURL(url);
    }, 1000);
  }

  function draw(button) {
    var source = button.getAttribute("data-chart-png");
    var name = button.getAttribute("data-filename") || "statistika.png";
    var image = new window.Image();
    button.setAttribute("aria-busy", "true");
    image.onload = function () {
      var canvas = document.createElement("canvas");
      canvas.width = image.naturalWidth * SCALE;
      canvas.height = image.naturalHeight * SCALE;
      var context = canvas.getContext("2d");
      context.scale(SCALE, SCALE);
      context.drawImage(image, 0, 0);
      canvas.toBlob(function (blob) {
        button.removeAttribute("aria-busy");
        if (blob) save(blob, name);
      }, "image/png");
    };
    image.onerror = function () {
      button.removeAttribute("aria-busy");
    };
    image.src = source;
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest && event.target.closest("[data-chart-png]");
    if (!button) return;
    event.preventDefault();
    draw(button);
    var holder = button.closest("details");
    if (holder) holder.open = false;
  });
})();
