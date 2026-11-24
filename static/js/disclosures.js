/*
 * Remembered blocks (app/accounts/disclosures.py, docs/adr/0194).
 *
 * The server draws every remembered `<details data-disclosure>` open or closed
 * for the person signed in, and says which state it stored
 * (`data-disclosure-saved`). This script only reports a change: when the reader
 * opens or closes one, the new state is posted to the endpoint the page names.
 * Without this script, or when the post fails, the block is still an ordinary
 * `<details>` — it opens and closes, and only the memory is missing.
 *
 * Three rules:
 *
 * - **A toggle that agrees with what is stored sends nothing.** The browser
 *   fires `toggle` for a block parsed with `open`, and that is not a choice.
 * - **One request per block at a time, the last state wins.** Quick toggles
 *   while a save is in flight collapse into one follow-up carrying the final
 *   state, so a slow first response can never overwrite a later choice.
 * - **Saved means 204.** A redirect to the sign-in page, a refusal or a network
 *   error leaves `data-disclosure-saved` as it was; nothing claims a state was
 *   kept that was not.
 *
 * And three ways a block is shown open or closed for one view without that
 * being a choice (UX-004, 2026-11-25). Each marks the block
 * `data-disclosure-shown` with the state it drew; a toggle that agrees with it
 * is the page's, not the reader's, and is not saved. The reader's first toggle
 * away from it clears the mark and is handled as usual.
 *
 * - The server draws it open for a view the reader asked for (the tag's
 *   `force_open`, e.g. the next page of a list inside it).
 * - A link to an element that is, or directly holds, a closed remembered block
 *   (`#varasemad`) opens it.
 * - `data-disclosure-keep`: when htmx redraws the page, the block stays as the
 *   reader had it on screen rather than snapping to the stored state — which
 *   may be a save still in flight, or a view-only state from above.
 */
(function () {
  "use strict";

  var meta = document.querySelector('meta[name="disclosure-endpoint"]');
  if (!meta) {
    return;
  }
  var endpoint = meta.getAttribute("content");
  var wanted = {};
  var inFlight = {};

  function markSaved(key, opened) {
    var blocks = document.querySelectorAll("details[data-disclosure]");
    for (var i = 0; i < blocks.length; i += 1) {
      if (blocks[i].getAttribute("data-disclosure") === key) {
        blocks[i].setAttribute("data-disclosure-saved", opened ? "1" : "0");
      }
    }
  }

  function send(key) {
    if (inFlight[key] || !Object.prototype.hasOwnProperty.call(wanted, key)) {
      return;
    }
    // The token the bar's own «Välju» form carries — present on every page
    // drawn for somebody signed in — so the page's markup is the same on every
    // render and no second copy of the token is needed.
    var field = document.querySelector('input[name="csrfmiddlewaretoken"]');
    var csrf = field ? field.value : "";
    var opened = wanted[key];
    delete wanted[key];
    inFlight[key] = true;
    var body = new URLSearchParams();
    body.append("key", key);
    body.append("open", opened ? "1" : "0");
    body.append("csrfmiddlewaretoken", csrf);
    fetch(endpoint, {
      method: "POST",
      body: body,
      credentials: "same-origin",
      redirect: "manual",
      keepalive: true,
      headers: { "X-CSRFToken": csrf },
    })
      .then(function (response) {
        if (response.status === 204) {
          markSaved(key, opened);
        }
      })
      .catch(function () {
        /* Not saved; the block itself still works. */
      })
      .then(function () {
        inFlight[key] = false;
        send(key);
      });
  }

  document.addEventListener(
    "toggle",
    function (event) {
      var block = event.target;
      if (!block || !block.matches || !block.matches("details[data-disclosure]")) {
        return;
      }
      var shown = block.getAttribute("data-disclosure-shown");
      if (shown !== null) {
        if (block.open === (shown === "1")) {
          return;
        }
        block.removeAttribute("data-disclosure-shown");
      }
      var key = block.getAttribute("data-disclosure");
      var saved = block.getAttribute("data-disclosure-saved") === "1";
      var queued = Object.prototype.hasOwnProperty.call(wanted, key);
      if (block.open === saved && !queued && !inFlight[key]) {
        return;
      }
      wanted[key] = block.open;
      send(key);
    },
    true
  );

  function showFor(block, opened) {
    if (block.open === opened) {
      return;
    }
    block.setAttribute("data-disclosure-shown", opened ? "1" : "0");
    block.open = opened;
  }

  function openTarget() {
    var id = window.location.hash.slice(1);
    if (!id) {
      return;
    }
    var target = null;
    try {
      target = document.getElementById(decodeURIComponent(id));
    } catch (error) {
      return;
    }
    if (!target) {
      return;
    }
    var block = target.matches("details[data-disclosure]")
      ? target
      : target.querySelector(":scope > details[data-disclosure]");
    if (block) {
      showFor(block, true);
    }
  }

  openTarget();
  window.addEventListener("hashchange", openTarget);

  var kept = null;
  document.addEventListener("htmx:beforeSwap", function () {
    var blocks = document.querySelectorAll("details[data-disclosure-keep][data-disclosure]");
    kept = {};
    for (var i = 0; i < blocks.length; i += 1) {
      kept[blocks[i].getAttribute("data-disclosure")] = blocks[i].open;
    }
  });
  document.addEventListener("htmx:afterSettle", function () {
    if (!kept) {
      return;
    }
    var blocks = document.querySelectorAll("details[data-disclosure-keep][data-disclosure]");
    for (var i = 0; i < blocks.length; i += 1) {
      var key = blocks[i].getAttribute("data-disclosure");
      if (Object.prototype.hasOwnProperty.call(kept, key)) {
        showFor(blocks[i], kept[key]);
      }
    }
    kept = null;
  });
})();
