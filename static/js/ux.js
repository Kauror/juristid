/*
 * Koda Õigusloome — behaviour added by the 2026-08-27 UX pass.
 *
 * The same rule app.js follows: nothing here is the only way to do anything.
 * Every keyboard move has a visible control that does the same thing, every
 * disclosure is a real <details>, and every date a chip fills in was computed
 * by the server in Europe/Tallinn and delivered on the element — this file
 * never works out what "next week" means.
 *
 * Its own file rather than more of app.js, for the reason ux.css is its own
 * file: the pass is additive and stays separable.
 */
(function () {
  "use strict";

  /* Bound once per element, so a re-bind after an HTMX swap costs nothing and
     duplicates no listener. Same idiom as app.js. */
  function once(element, name) {
    var key = "uxbound" + name;
    if (element.dataset[key]) {
      return false;
    }
    element.dataset[key] = "1";
    return true;
  }

  /* Whether a keystroke belongs to whatever the person is editing. A shortcut
     that fires inside a text box is a shortcut that eats text. */
  function isEditing(target) {
    if (!target || !target.closest) {
      return false;
    }
    if (target.isContentEditable) {
      return true;
    }
    var name = (target.tagName || "").toLowerCase();
    if (name === "input" || name === "textarea" || name === "select") {
      return true;
    }
    /* Inside an open popover or dialog the keys belong to it. */
    return Boolean(target.closest("dialog, [role=dialog], details[open] form"));
  }

  /* ---- The composer, closed by default ----------------------------------
   * `L` opens it and puts the cursor in the box. The closed row itself is a
   * <summary>, so the mouse and the keyboard already open it without this.
   */
  function openComposer(composer) {
    if (!composer) {
      return;
    }
    composer.open = true;
    var box = composer.querySelector("textarea");
    if (box) {
      box.focus();
    }
  }

  /* Any control that sends the reader to a collapsed disclosure has to open it
     first. app.js focuses the first field inside a `[data-focus]` target, and a
     field inside a closed <details> is not focusable — «Muuda» on the
     Järgmiseks row would scroll to a shut box and leave the cursor where it
     was. Capture phase, so this runs before that handler. */
  document.addEventListener(
    "click",
    function (event) {
      var trigger = event.target.closest ? event.target.closest("[data-focus]") : null;
      if (!trigger) {
        return;
      }
      var target = document.getElementById(trigger.getAttribute("data-focus"));
      if (target && target.tagName === "DETAILS") {
        target.open = true;
      }
    },
    true
  );

  document.addEventListener("keydown", function (event) {
    if (event.ctrlKey || event.metaKey || event.altKey || isEditing(event.target)) {
      return;
    }
    if (event.key !== "l" && event.key !== "L") {
      return;
    }
    var composer = document.querySelector("details.uxcomp");
    if (!composer) {
      return;
    }
    event.preventDefault();
    openComposer(composer);
  });

  /* ---- Quick dates in the composer --------------------------------------
   * Each chip carries the date the server resolved for it, written the way the
   * Estonian date control reads it, plus the label to show once it is chosen
   * ("+1 nädal → N 03.09"). Clicking one fills the exact-date field, which is
   * the field that is actually submitted and validated — the chip is a faster
   * way to type into it and nothing more.
   */
  function bindQuickDates(scope) {
    scope.querySelectorAll("[data-quickdate-group]").forEach(function (group) {
      if (!once(group, "QuickDate")) {
        return;
      }
      var field = document.getElementById(group.getAttribute("data-quickdate-group"));
      if (!field) {
        return;
      }
      var chips = group.querySelectorAll("[data-quickdate]");
      var sync = function () {
        chips.forEach(function (chip) {
          var chosen = chip.getAttribute("data-quickdate") === field.value;
          chip.classList.toggle("is-selected", chosen);
          chip.setAttribute("aria-pressed", chosen ? "true" : "false");
          var label = chip.getAttribute(chosen ? "data-label-chosen" : "data-label-default");
          if (label) {
            chip.textContent = label;
          }
        });
      };
      chips.forEach(function (chip) {
        chip.addEventListener("click", function () {
          field.value = chip.getAttribute("data-quickdate");
          field.dispatchEvent(new Event("change", { bubbles: true }));
          sync();
        });
      });
      field.addEventListener("change", sync);
      field.addEventListener("input", sync);
      sync();
    });
  }

  /* ---- Minu töö: J/K move, X completes, Enter opens ----------------------
   * The visible equivalents are all on the row already: the ✓ button, the ⋯
   * menu and the row's own link. The hint strip under the list says so.
   */
  function workRows() {
    return Array.prototype.slice.call(document.querySelectorAll("[data-workrow]"));
  }

  function selectRow(rows, index) {
    rows.forEach(function (row, position) {
      row.classList.toggle("is-selected", position === index);
    });
    var row = rows[index];
    if (!row) {
      return;
    }
    /* The link is what carries the row's accessible name, so focusing it is
       what tells a screen reader where the selection went. `auto` lets the
       browser honour the reader's own reduced-motion setting. */
    var link = row.querySelector("a");
    if (link) {
      link.focus({ preventScroll: true });
    }
    row.scrollIntoView({ block: "nearest", behavior: "auto" });
  }

  function currentIndex(rows) {
    for (var i = 0; i < rows.length; i += 1) {
      if (rows[i].classList.contains("is-selected")) {
        return i;
      }
    }
    return -1;
  }

  document.addEventListener("keydown", function (event) {
    if (event.ctrlKey || event.metaKey || event.altKey || isEditing(event.target)) {
      return;
    }
    var key = event.key.toLowerCase();
    /* J/K/Enter only. `X` went with the ✓ button it pressed: completing a step
       without setting the follow-up is half a transaction, so completion now
       goes through the ⋯ menu to the Matter page, and a shortcut for a control
       that is not on the row is a shortcut that does nothing
       (01-EHITUSJUHIS §3.6). */
    if (key !== "j" && key !== "k" && event.key !== "Enter") {
      return;
    }
    var rows = workRows();
    if (!rows.length) {
      return;
    }
    var index = currentIndex(rows);

    if (key === "j" || key === "k") {
      event.preventDefault();
      var next = index < 0 ? (key === "j" ? 0 : rows.length - 1) : index + (key === "j" ? 1 : -1);
      selectRow(rows, Math.max(0, Math.min(rows.length - 1, next)));
      return;
    }

    if (index < 0) {
      return;
    }
    var row = rows[index];
    /* Enter on the row link is the browser's own behaviour; this only matters
       when the focus ring sits somewhere else in the row. */
    if (event.target.closest && event.target.closest("a")) {
      return;
    }
    var open = row.querySelector("a[href]");
    if (open) {
      event.preventDefault();
      open.click();
    }
  });

  /* Clicking or focusing a row makes it the selected one, so the keys carry on
     from wherever the reader actually is. */
  function bindWorkRows(scope) {
    scope.querySelectorAll("[data-workrow]").forEach(function (row) {
      if (!once(row, "WorkRow")) {
        return;
      }
      row.addEventListener("focusin", function () {
        selectRow(workRows(), workRows().indexOf(row));
      });
    });
  }

  /* ---- One popover open at a time ---------------------------------------
   * The register's Määra menus and the Järgmiseks defer menu are absolutely
   * positioned <details>. Leaving several open stacks two menus over the same
   * rows. Escape closes the one you are in and returns focus to its trigger,
   * which is what every other disclosure in the application does (app.js).
   */
  /* A menu that has to escape a scroll container.

     The register's table lives in `.tablewrap`, which is `overflow-x: auto` so
     a wide table can scroll — and a scroll container clips its absolutely
     positioned descendants in both axes, so an owner menu anchored to a row was
     cut off one line below its trigger. The box is `position: fixed` in the
     stylesheet; this puts it under its trigger and keeps it inside the window.
     With no script it still renders at its static position, which is under the
     trigger — wrong by a few pixels rather than unusable. */
  function place(holder) {
    var menu = holder.querySelector("[data-uxfloat]");
    var trigger = holder.querySelector("summary");
    if (!menu || !trigger) {
      return;
    }
    var anchor = trigger.getBoundingClientRect();
    var margin = 8;
    menu.style.top = anchor.bottom + 4 + "px";
    var left = Math.min(anchor.left, window.innerWidth - menu.offsetWidth - margin);
    menu.style.left = Math.max(margin, left) + "px";
  }

  function placeOpenPopovers() {
    document.querySelectorAll("details[data-uxpopover][open]").forEach(place);
  }

  function bindExclusivePopovers(scope) {
    scope.querySelectorAll("details[data-uxpopover]").forEach(function (holder) {
      if (!once(holder, "Popover")) {
        return;
      }
      holder.addEventListener("toggle", function () {
        if (!holder.open) {
          return;
        }
        document.querySelectorAll("details[data-uxpopover][open]").forEach(function (other) {
          if (other !== holder) {
            other.open = false;
          }
        });
        place(holder);
      });
    });
  }

  /* Capture, so a scroll inside the table moves the menu with its row rather
     than leaving it behind. */
  window.addEventListener("scroll", placeOpenPopovers, true);
  window.addEventListener("resize", placeOpenPopovers);

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape" || !event.target.closest) {
      return;
    }
    var holder = event.target.closest("details[data-uxpopover][open]");
    if (!holder) {
      return;
    }
    event.preventDefault();
    holder.open = false;
    var trigger = holder.querySelector("summary");
    if (trigger) {
      trigger.focus();
    }
  });

  document.addEventListener("click", function (event) {
    document.querySelectorAll("details[data-uxpopover][open]").forEach(function (holder) {
      if (!holder.contains(event.target)) {
        holder.open = false;
      }
    });
  });

  /* ---- «Salvesta praegune filter vaatena» --------------------------------
   * The view is the address. The control shows the current canonical URL and
   * offers to copy it; there is no stored view because there is nothing to
   * store — the link is the whole thing (matter_list.html).
   */
  function bindCopyLink(scope) {
    scope.querySelectorAll("[data-copy-from]").forEach(function (button) {
      if (!once(button, "Copy")) {
        return;
      }
      button.addEventListener("click", function () {
        var field = document.getElementById(button.getAttribute("data-copy-from"));
        if (!field) {
          return;
        }
        field.select();
        field.setSelectionRange(0, field.value.length);
        var done = function () {
          var said = button.getAttribute("data-label-done");
          if (!said) {
            return;
          }
          var before = button.textContent;
          button.textContent = said;
          window.setTimeout(function () {
            button.textContent = before;
          }, 2000);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(field.value).then(done, function () {});
          return;
        }
        /* Older engines, and any context where the async API is refused. The
           text is selected either way, so Ctrl+C still works. */
        try {
          if (document.execCommand("copy")) {
            done();
          }
        } catch (error) {
          /* Selected and visible is a working fallback. */
        }
      });
    });
  }

  /* ------------------------------------------------------------------
     Aktiivsed teemad — the scoped quick filter.

     A narrowing over rows that are already on the page, not a search: the
     person's whole open portfolio is rendered, so there is nothing to fetch and
     nothing to submit. With scripting off the input simply does nothing and
     every row stays visible, which is the correct fallback for a control whose
     only job is to hide some of them (design handoff, Minu asjad §G).
     ------------------------------------------------------------------ */
  function bindRowFilter(root) {
    var inputs = root.querySelectorAll("[data-filter-rows]");
    Array.prototype.forEach.call(inputs, function (input) {
      if (input.dataset.filterBound === "1") {
        return;
      }
      input.dataset.filterBound = "1";
      var selector = input.getAttribute("data-filter-rows");
      input.addEventListener("input", function () {
        var needle = input.value.trim().toLowerCase();
        var groups = document.querySelectorAll(selector);
        Array.prototype.forEach.call(groups, function (group) {
          Array.prototype.forEach.call(group.children, function (row) {
            var text = (row.textContent || "").toLowerCase();
            row.hidden = needle !== "" && text.indexOf(needle) === -1;
          });
        });
      });
    });
  }

  function bindAll(scope) {
    var root = scope && scope.querySelectorAll ? scope : document;
    bindQuickDates(root);
    bindWorkRows(root);
    bindExclusivePopovers(root);
    bindCopyLink(root);
    bindRowFilter(root);
  }

  document.addEventListener("DOMContentLoaded", function () {
    bindAll(document);
  });

  document.body.addEventListener("htmx:afterSwap", function (event) {
    bindAll(event.target);
  });
})();
