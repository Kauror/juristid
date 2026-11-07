/* header-menu.js: UI-03 header settings menu (agent B). Bound once, idempotently, and again after every HTMX swap, like app.js.
 *
 * «Seaded» is a real `<details>` (templates/components/settings_menu.html), so
 * it opens and its links work with this file absent. What is added here is
 * what a disclosure does not do by itself:
 *
 *   - Escape closes it and puts the focus back on the gear;
 *   - a click outside closes it — and if that click left the focus nowhere
 *     (on the page itself, or still inside the closed menu), the focus goes
 *     back to the gear rather than to the top of the document;
 *   - moving the keyboard out of it closes it, without taking the focus from
 *     wherever it was going;
 *   - only one top-bar menu is open at a time.
 *
 * **One menu at a time** is the shared `juristid:menu-open` contract with the
 * notification panel (UI-01): whichever opens announces itself with
 * `document.dispatchEvent(new CustomEvent("juristid:menu-open",
 * {detail: {id}}))`, and every other closes when it hears a different id. The
 * two other popups on the bar take part too, so no pair of them can overlap:
 * the persona popover (bound in app.js; it is watched here, not rewritten) and
 * «Veel».
 *
 * Every listener is on `document` and every lookup happens at event time, so
 * there is nothing to rebind after an htmx swap and no second copy to leave
 * behind. `toggle` does not bubble, which is why it is caught in the capture
 * phase.
 */
(function () {
  "use strict";

  var EVENT = "juristid:menu-open";
  var SETTINGS = "settings-menu";
  var PERSONA = "persona-menu";
  var MORE = "topnav-more";

  function announce(id) {
    document.dispatchEvent(new CustomEvent(EVENT, { detail: { id: id } }));
  }

  function settingsMenus() {
    return Array.prototype.slice.call(document.querySelectorAll("[data-settings-menu]"));
  }

  function trigger(menu) {
    return menu.querySelector(".settingsmenu__trigger");
  }

  /* `restore`: put the focus back on the gear. Never when the focus has
     already gone somewhere a person chose. */
  function closeSettings(menu, restore) {
    if (!menu.open) {
      return;
    }
    menu.open = false;
    if (restore) {
      var gear = trigger(menu);
      if (gear) {
        gear.focus();
      }
    }
  }

  function closePersona() {
    document.querySelectorAll("[data-persona-trigger]").forEach(function (pill) {
      if (pill.getAttribute("aria-expanded") !== "true") {
        return;
      }
      pill.setAttribute("aria-expanded", "false");
      var popover = document.getElementById(pill.getAttribute("aria-controls"));
      if (popover) {
        popover.hidden = true;
      }
    });
  }

  function closeMore() {
    document.querySelectorAll(".topnav__more[open]").forEach(function (more) {
      more.open = false;
    });
  }

  /* Opening announces; closing does not. */
  document.addEventListener(
    "toggle",
    function (event) {
      var target = event.target;
      if (!target || !target.open || !target.matches) {
        return;
      }
      if (target.matches("[data-settings-menu]")) {
        announce(SETTINGS);
      } else if (target.matches(".topnav__more")) {
        announce(MORE);
      }
    },
    true
  );

  /* The persona popover is opened by app.js, which says so on the pill's
     `aria-expanded`. Watching that attribute is the one way to hear it open
     without a second click handler racing the first. */
  if (window.MutationObserver) {
    new MutationObserver(function (records) {
      records.forEach(function (record) {
        var pill = record.target;
        if (
          pill.matches &&
          pill.matches("[data-persona-trigger]") &&
          pill.getAttribute("aria-expanded") === "true" &&
          record.oldValue !== "true"
        ) {
          announce(PERSONA);
        }
      });
    }).observe(document.documentElement, {
      subtree: true,
      attributes: true,
      attributeFilter: ["aria-expanded"],
      attributeOldValue: true,
    });
  }

  document.addEventListener(EVENT, function (event) {
    var id = event.detail && event.detail.id;
    if (id !== SETTINGS) {
      settingsMenus().forEach(function (menu) {
        closeSettings(menu, false);
      });
    }
    if (id !== PERSONA) {
      closePersona();
    }
    if (id !== MORE) {
      closeMore();
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") {
      return;
    }
    settingsMenus().forEach(function (menu) {
      if (!menu.open) {
        return;
      }
      event.preventDefault();
      var active = document.activeElement;
      closeSettings(menu, !active || active === document.body || menu.contains(active));
    });
  });

  document.addEventListener("click", function (event) {
    settingsMenus().forEach(function (menu) {
      if (!menu.open || menu.contains(event.target)) {
        return;
      }
      var active = document.activeElement;
      closeSettings(menu, !active || active === document.body || menu.contains(active));
    });
  });

  /* Tabbing out closes it. `focusin` fires on the element gaining focus, so
     the check needs no timer. */
  document.addEventListener("focusin", function (event) {
    settingsMenus().forEach(function (menu) {
      if (menu.open && !menu.contains(event.target)) {
        closeSettings(menu, false);
      }
    });
  });
})();
