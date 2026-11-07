/* notification-panel.js: UI-01 notification panel (agent A). Bound once, idempotently, and again after every HTMX swap, like app.js.
 *
 * The bell on the top bar is a link to /teavitused/ (templates/notifications/
 * _topbar_bell.html). This turns it into the button that opens the panel under
 * it, and nothing else — everything inside the panel is server-rendered HTML
 * with ordinary htmx attributes:
 *
 *  - **Opening fetches; it never writes.** Each opening asks for the panel
 *    afresh with a GET (`data-panel-url`), so what it shows is current, and
 *    no item changes state because it was shown.
 *  - **Keyboard.** The trigger is a button to assistive technology
 *    (`aria-expanded`, `aria-controls`); Enter and Space open it, focus moves
 *    to the panel's heading, Tab walks the items, Escape closes it and puts
 *    focus back on the bell. An Escape something inside the panel has already
 *    handled — a `⋯` menu, the «Lükka edasi» date panel — closes only that
 *    (`defaultPrevented`, the rule the rest of the application follows).
 *  - **Outside click** closes it. Focus goes back to the bell when it was in
 *    the panel; a click that put focus somewhere else keeps it there.
 *  - **One menu at a time.** Opening says so with `juristid:menu-open`, and a
 *    `juristid:menu-open` from anything else (the settings menu) closes the
 *    panel — the contract with the header (agent B).
 *
 * Without this script, or before it runs, the bell is the link it always was.
 */
(function () {
  "use strict";

  var MENU_ID = "notification-panel";
  var OPEN_EVENT = "juristid:menu-open";

  function once(element, name) {
    var key = "notifbound" + name;
    if (element.dataset[key]) {
      return false;
    }
    element.dataset[key] = "1";
    return true;
  }

  function parts(bell) {
    return {
      trigger: bell.querySelector("[data-notifbell-trigger]"),
      panel: bell.querySelector("[data-notifpanel]"),
      content: bell.querySelector("[data-notifpanel-content]"),
    };
  }

  function isOpen(bell) {
    var trigger = parts(bell).trigger;
    return Boolean(trigger) && trigger.getAttribute("aria-expanded") === "true";
  }

  function focusHeading(bell) {
    var heading = bell.querySelector("[data-notifpanel-content] .notifpanel__title");
    if (heading) {
      try {
        heading.focus({ preventScroll: true });
      } catch (error) {
        heading.focus();
      }
    }
  }

  function load(bell) {
    var p = parts(bell);
    var url = p.trigger.getAttribute("data-panel-url");
    if (!url || !window.htmx || !window.htmx.ajax) {
      return false;
    }
    bell.notifFocusOnLoad = true;
    /* Every opening fetches the list afresh, and the previous list stays on
       screen until it arrives. Say so, so nothing (a person or a test) acts
       on rows that are about to be replaced. */
    p.content.setAttribute("aria-busy", "true");
    window.htmx.ajax("GET", url, {
      source: p.content,
      target: p.content,
      swap: "innerHTML",
      select: "[data-notifpanel-body]",
    });
    return true;
  }

  function open(bell) {
    var p = parts(bell);
    if (!load(bell)) {
      /* No htmx: the link is the way in. */
      window.location.assign(p.trigger.getAttribute("href"));
      return;
    }
    p.panel.hidden = false;
    p.trigger.setAttribute("aria-expanded", "true");
    focusHeading(bell);
    document.dispatchEvent(new CustomEvent(OPEN_EVENT, { detail: { id: MENU_ID } }));
  }

  function close(bell, restoreFocus) {
    if (!isOpen(bell)) {
      return;
    }
    var p = parts(bell);
    /* An open `⋯` inside goes with it, so the next opening starts closed. */
    p.panel.querySelectorAll("details[open]").forEach(function (holder) {
      holder.open = false;
    });
    p.panel.hidden = true;
    p.trigger.setAttribute("aria-expanded", "false");
    bell.notifFocusOnLoad = false;
    if (restoreFocus) {
      p.trigger.focus();
    }
  }

  function bindBells(scope) {
    (scope || document).querySelectorAll("[data-notifbell]").forEach(function (bell) {
      var p = parts(bell);
      if (!p.trigger || !p.panel || !p.content || !once(bell, "Bell")) {
        return;
      }
      p.trigger.setAttribute("role", "button");
      p.trigger.setAttribute("aria-expanded", "false");
      p.trigger.setAttribute("aria-controls", p.panel.id);

      p.trigger.addEventListener("click", function (event) {
        /* A modified click still opens the list in a new tab, as a link does. */
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.button === 1) {
          return;
        }
        event.preventDefault();
        if (isOpen(bell)) {
          close(bell, false);
        } else {
          open(bell);
        }
      });

      /* A link answers Enter by itself; a button also answers Space. */
      p.trigger.addEventListener("keydown", function (event) {
        if (event.key === " " || event.key === "Spacebar") {
          event.preventDefault();
          p.trigger.click();
        }
      });

      /* The panel's own content arrived — on an opening, or as the answer to
         «Märgi kõik loetuks»: focus its heading. An item's answer and a
         further page are placed by the handlers further down. */
      ["htmx:afterSettle", "htmx:responseError", "htmx:sendError"].forEach(function (name) {
        p.content.addEventListener(name, function (event) {
          if (event.target === p.content) {
            p.content.removeAttribute("aria-busy");
          }
        });
      });
      p.content.addEventListener("htmx:afterSettle", function (event) {
        if (!p.content.contains(event.target)) {
          return;
        }
        var config = event.detail && event.detail.requestConfig;
        var source = config && config.elt;
        var markAll = Boolean(source && source.closest && source.closest(".notifpanel__markall"));
        if (!bell.notifFocusOnLoad && !markAll) {
          return;
        }
        bell.notifFocusOnLoad = false;
        if (isOpen(bell)) {
          focusHeading(bell);
        }
      });
    });
  }

  function eachOpenBell(callback) {
    document.querySelectorAll("[data-notifbell]").forEach(function (bell) {
      if (isOpen(bell)) {
        callback(bell);
      }
    });
  }

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape" || event.defaultPrevented) {
      return;
    }
    eachOpenBell(function (bell) {
      event.preventDefault();
      close(bell, true);
    });
  });

  document.addEventListener("click", function (event) {
    eachOpenBell(function (bell) {
      if (bell.contains(event.target)) {
        return;
      }
      var panel = parts(bell).panel;
      var active = document.activeElement;
      var focusWasInside = !active || active === document.body || panel.contains(active);
      close(bell, focusWasInside);
    });
  });

  document.addEventListener(OPEN_EVENT, function (event) {
    var id = event.detail && event.detail.id;
    if (id === MENU_ID) {
      return;
    }
    eachOpenBell(function (bell) {
      close(bell, false);
    });
  });

  /* ---- Focus after an answer or a further page inside the panel ----------
   * The panel and the Minu asjad block never carry the application's own
   * `data-focus-after-save` (static/js/app.js): after an `outerHTML` swap that
   * rule searches the whole document for the first one, and a panel in the top
   * bar — open or long closed — would come before every landing in `<main>`
   * and take it (QA-018). They mark their own landings `data-notif-focus` and
   * place focus here instead: after a read, unread or postponement, the same
   * item's `⋯` (or the item, when it has none left); after «Näita
   * varasemaid», the first item that arrived. These run after app.js's
   * handler and have the last word inside the panel and the block only. */
  var pendingPage = null;

  document.addEventListener("htmx:beforeRequest", function (event) {
    var source = event.detail && event.detail.elt;
    var more = source && source.closest ? source.closest("[data-notifpanel-more]") : null;
    if (!more) {
      return;
    }
    var list = more.closest("[data-notifpanel-list]");
    pendingPage = list ? { list: list, seen: list.querySelectorAll("[data-notif-item]").length } : null;
  });

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

  /* The Minu asjad block answered in place and the last item went with it: the
     block is gone, so focus would fall to the page. It goes to the rail block
     that now stands where it stood. */
  document.addEventListener("htmx:afterSettle", function (event) {
    var wrapper = event.target;
    if (!wrapper || wrapper.id !== "notif-sidebar") {
      return;
    }
    /* The wrapper itself counts as lost: app.js lands on the swapped element
       when it finds no landing of its own, and this one draws no box. */
    var active = document.activeElement;
    if (active && active !== document.body && active !== wrapper && document.contains(active)) {
      return;
    }
    var landing = wrapper.querySelector("[data-notif-focus]") || wrapper.nextElementSibling;
    if (landing) {
      if (!landing.matches("a[href], button, summary, input") && !landing.hasAttribute("tabindex")) {
        landing.setAttribute("tabindex", "-1");
      }
      focusQuietly(landing);
    }
  });

  document.addEventListener("htmx:afterSettle", function (event) {
    var item = event.target;
    if (!item || !item.matches || !item.matches("[data-notif-item]") || !item.closest("[data-notifpanel]")) {
      return;
    }
    if (pendingPage) {
      var arrived = pendingPage.list.querySelectorAll("[data-notif-item]")[pendingPage.seen];
      pendingPage = null;
      if (arrived) {
        focusQuietly(arrived.querySelector(".notifitem__title") || arrived);
      }
      return;
    }
    var config = event.detail && event.detail.requestConfig;
    if (!config || String(config.verb).toLowerCase() !== "post") {
      return;
    }
    focusQuietly(item.querySelector(".notifitem__menubutton") || item);
  });

  function start(scope) {
    try {
      bindBells(scope);
    } catch (error) {
      if (window.console && window.console.error) {
        window.console.error("juristid: notificationPanel failed", error);
      }
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    start(document);
  });

  document.addEventListener("htmx:afterSwap", function (event) {
    var target = event.target;
    start(target && target.querySelectorAll ? target : document);
  });
})();
