/* matter-preview.js: «Eelvaade» (UX-006) — one Teema in a drawer beside the register.
 *
 * A row's quiet icon (`[data-mpreview-open]`, templates/matters/partials/
 * matter_row.html) opens the one drawer in base.html (`[data-mpreview]`) and
 * fills it with the read-only fragment `matters:matter_preview`. The list is
 * never re-rendered and the address never changes, so its search, filters,
 * order, page and scroll position are exactly where they were when the drawer
 * closes (decision N-C2: no history entry).
 *
 *  - **Opening fetches one Teema, on demand.** Nothing is prefetched; a later
 *    opening asks again, so what it shows is current. A response that arrives
 *    after another opening or a close is dropped.
 *  - **Keyboard.** Focus moves into the drawer and Tab stays inside it while it
 *    is open (the calendar panel's rule, static/js/ux.js `bindCalendar`);
 *    Escape, the ✕ and a click on the backdrop close it and focus goes back to
 *    the icon it was opened from. An Escape something inside has already
 *    handled closes only that (`defaultPrevented`).
 *  - **One panel at a time.** Opening says so with `juristid:menu-open`, and
 *    one from anything else closes the drawer (the notification panel's
 *    contract, static/js/notification-panel.js).
 *  - **Last viewed** is the reader's own browser session (`sessionStorage`),
 *    never the address and never the server: the row of the Teema last
 *    previewed or opened is marked when the list is drawn again. Returning to a
 *    narrowed list from that Teema by a link rather than by Back (Back restores
 *    the scroll by itself) brings the row into view.
 */
(function () {
  "use strict";

  var PANEL_ID = "matter-preview";
  var OPEN_EVENT = "juristid:menu-open";
  var LAST_KEY = "juristid:teema-viimati";
  var MARK_CLASS = "is-lastviewed";
  var TEEMA_PATH = /^\/teemad\/([0-9a-f-]{36})\//;

  var state = { trigger: null, matterId: "", request: 0 };

  function shell() {
    return document.querySelector("[data-mpreview]");
  }

  function isOpen() {
    var drawer = shell();
    return Boolean(drawer) && !drawer.hidden;
  }

  function remember(id) {
    try {
      window.sessionStorage.setItem(LAST_KEY, id);
    } catch (error) {
      /* Storage off: the marker is a convenience, the list works without it. */
    }
  }

  function remembered() {
    try {
      return window.sessionStorage.getItem(LAST_KEY) || "";
    } catch (error) {
      return "";
    }
  }

  function rowOf(id) {
    return id ? document.querySelector('[data-matter-row="' + id + '"]') : null;
  }

  function mark(scope) {
    var id = remembered();
    (scope || document).querySelectorAll("tr." + MARK_CLASS).forEach(function (row) {
      if (row.getAttribute("data-matter-row") !== id) {
        row.classList.remove(MARK_CLASS);
      }
    });
    var row = rowOf(id);
    if (row) {
      row.classList.add(MARK_CLASS);
    }
    return row;
  }

  function idFromUrl(url) {
    var match = /\/teemad\/([0-9a-f-]{36})\/eelvaade\//.exec(url || "");
    return match ? match[1] : "";
  }

  function focusables(panel) {
    return Array.prototype.filter.call(
      panel.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"]), summary'
      ),
      function (element) {
        return element.offsetParent !== null || element === document.activeElement;
      }
    );
  }

  function focusQuietly(element) {
    if (!element) {
      return;
    }
    try {
      element.focus({ preventScroll: true });
    } catch (error) {
      element.focus();
    }
  }

  function showMessage(content, text, href) {
    content.textContent = "";
    var paragraph = document.createElement("p");
    paragraph.className = "mpreview__error";
    paragraph.textContent = text + " ";
    if (href) {
      var link = document.createElement("a");
      link.href = href;
      link.textContent = "Ava teema →";
      paragraph.appendChild(link);
    }
    content.appendChild(paragraph);
  }

  function open(trigger) {
    var drawer = shell();
    var url = trigger.getAttribute("data-mpreview-open");
    if (!drawer || !url || !window.fetch) {
      return;
    }
    var content = drawer.querySelector("[data-mpreview-content]");
    var panel = drawer.querySelector("[data-mpreview-panel]");
    var row = trigger.closest("tr");
    var titleLink = row ? row.querySelector(".table__titlelink") : null;
    var fullHref = titleLink ? titleLink.getAttribute("href") : "";
    var request = ++state.request;

    state.trigger = trigger;
    state.matterId = idFromUrl(url);
    trigger.setAttribute("aria-expanded", "true");
    content.setAttribute("aria-busy", "true");
    showMessage(content, "Laen eelvaadet…", "");
    drawer.hidden = false;
    focusQuietly(drawer.querySelector("#mpreview-heading"));
    document.dispatchEvent(new CustomEvent(OPEN_EVENT, { detail: { id: PANEL_ID } }));
    if (state.matterId) {
      remember(state.matterId);
    }

    window
      .fetch(url, { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
      .then(function (response) {
        if (!response.ok) {
          throw new Error(String(response.status));
        }
        return response.text();
      })
      .then(function (html) {
        if (request !== state.request || !isOpen()) {
          return;
        }
        var parsed = new DOMParser().parseFromString(html, "text/html");
        var body = parsed.querySelector("[data-mpreview-body]");
        if (!body) {
          throw new Error("empty");
        }
        content.textContent = "";
        content.appendChild(document.importNode(body, true));
        content.removeAttribute("aria-busy");
        if (panel) {
          panel.scrollTop = 0;
        }
        focusQuietly(content.querySelector("#mpreview-title"));
      })
      .catch(function () {
        if (request !== state.request || !isOpen()) {
          return;
        }
        content.removeAttribute("aria-busy");
        showMessage(content, "Eelvaadet ei saanud avada.", fullHref);
      });
  }

  function close(restoreFocus) {
    var drawer = shell();
    if (!drawer || drawer.hidden) {
      return;
    }
    state.request += 1;
    drawer.hidden = true;
    var content = drawer.querySelector("[data-mpreview-content]");
    if (content) {
      content.textContent = "";
      content.removeAttribute("aria-busy");
    }
    var trigger = state.trigger;
    state.trigger = null;
    mark(document);
    if (trigger) {
      trigger.setAttribute("aria-expanded", "false");
    }
    if (!restoreFocus) {
      return;
    }
    if (trigger && document.body.contains(trigger)) {
      focusQuietly(trigger);
      return;
    }
    /* The list was redrawn meanwhile: the same Teema's icon, if it is there. */
    var row = rowOf(state.matterId);
    focusQuietly(row ? row.querySelector("[data-mpreview-open]") : null);
  }

  function bind() {
    var drawer = shell();
    if (!drawer || drawer.dataset.mpreviewBound) {
      return;
    }
    drawer.dataset.mpreviewBound = "1";

    document.addEventListener("click", function (event) {
      var trigger = event.target.closest ? event.target.closest("[data-mpreview-open]") : null;
      if (trigger) {
        event.preventDefault();
        open(trigger);
        return;
      }
      if (event.target.closest && event.target.closest("[data-mpreview-dismiss]")) {
        event.preventDefault();
        close(true);
      }
    });

    document.addEventListener("keydown", function (event) {
      if (!isOpen()) {
        return;
      }
      if (event.key === "Escape" || event.key === "Esc") {
        if (event.defaultPrevented) {
          return;
        }
        event.preventDefault();
        close(true);
        return;
      }
      if (event.key !== "Tab") {
        return;
      }
      var panel = drawer.querySelector("[data-mpreview-panel]");
      var items = focusables(panel);
      if (!items.length) {
        event.preventDefault();
        return;
      }
      var first = items[0];
      var last = items[items.length - 1];
      var inside = panel.contains(document.activeElement);
      if (event.shiftKey && (!inside || document.activeElement === first || document.activeElement === panel.querySelector("#mpreview-heading"))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (!inside || document.activeElement === last)) {
        event.preventDefault();
        first.focus();
      }
    });

    document.addEventListener(OPEN_EVENT, function (event) {
      if (!event.detail || event.detail.id !== PANEL_ID) {
        close(false);
      }
    });

    /* A redrawn list (the live search, a page of results) marks its row again. */
    document.body.addEventListener("htmx:afterSettle", function () {
      mark(document);
    });
  }

  function arrive() {
    var here = TEEMA_PATH.exec(window.location.pathname);
    if (here) {
      /* On a Teema itself: this is now the one last viewed. */
      remember(here[1]);
      return;
    }
    var row = mark(document);
    if (!row || !window.location.search) {
      return;
    }
    var navigation = window.performance && window.performance.getEntriesByType
      ? window.performance.getEntriesByType("navigation")[0]
      : null;
    if (navigation && navigation.type === "back_forward") {
      return;
    }
    var from = "";
    try {
      from = new URL(document.referrer).pathname;
    } catch (error) {
      from = "";
    }
    var came = TEEMA_PATH.exec(from);
    if (came && came[1] === row.getAttribute("data-matter-row") && !window.location.hash) {
      row.scrollIntoView({ block: "center" });
    }
  }

  function start() {
    bind();
    arrive();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
