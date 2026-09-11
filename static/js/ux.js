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

  /* ---- Arriving at the next step from another page ----------------------
   * `Määra` on Minu asjad, and `Muuda` / `Märgi tehtuks` / `Vaatasin üle…` in a
   * work row's menu, are the product's most repeated request — and all four are
   * links to *another* page, so the `[data-focus]` click handler below can
   * never run for them. They used to name `#jargmiseks`, which is not an id
   * anything renders; the browser found nothing, and arrival left the reader at
   * the top of the document with the composer shut (UX-003).
   *
   * They now name the two elements that actually exist, and this reproduces on
   * arrival exactly what clicking a `[data-focus]` control produces on the page
   * itself — one behaviour, not two implementations of it.
   *
   * The two destinations are not interchangeable, because the control each link
   * promises lives in a different place: recording that a task is done is
   * `PRAEGUNE TEGEVUS`, and *changing what the task is* is the `Muuda` /
   * `+ Järgmine tegevus` disclosure (docs/adr/0075 §3, §9).
   *
   * Those write surfaces render only for a writer on an open Matter
   * (matters/partials/overview.html), so `#lisa-jargmine` can legitimately be
   * absent. Falling back to `#praegune-tegevus` — which every branch renders —
   * is what stops a link from stranding the browser at `scrollY = 0` the way the
   * broken fragment did. This never opens a panel on an ordinary page load: it
   * runs once, on a hash somebody followed deliberately.
   *
   * **On `load`, not on `DOMContentLoaded`, and that is not a detail.**
   * Navigating to a fragment makes the browser run its own focusing steps on
   * the indicated element, and a `<details>` is not focusable — so the browser
   * puts focus back on `<body>`. It does that *after* `DOMContentLoaded`, which
   * silently undid the focus this sets: measured in Chromium, the composer
   * opened correctly and the caret was on the body. Running last is the only
   * order in which the focus survives. */
  var NEXT_STEP_TARGETS = ["praegune-tegevus", "lisa-jargmine"];

  function focusQuietly(element) {
    /* The scroll is ours, just above; focusing again would fight it. */
    try {
      element.focus({ preventScroll: true });
    } catch (error) {
      element.focus();
    }
  }

  function arriveAtNextStep() {
    var wanted = (window.location.hash || "").slice(1);
    if (NEXT_STEP_TARGETS.indexOf(wanted) === -1) {
      return;
    }
    var target = document.getElementById(wanted) || document.getElementById("praegune-tegevus");
    if (!target) {
      return;
    }
    if (target.tagName === "DETAILS") {
      /* Open before scrolling, so the box is its real height when it is
         centred, and so the field inside it is focusable at all. */
      target.open = true;
      target.scrollIntoView({ block: "center", behavior: "auto" });
      /* The same query app.js uses for `[data-focus]`. Not `input` in general:
         every form here opens with a hidden CSRF token. */
      var box = target.querySelector("textarea, select, input:not([type=hidden])");
      if (box) {
        box.focus();
      }
      return;
    }
    /* `PRAEGUNE TEGEVUS`. The control this link promised is the box that
       records what was done, so that is what takes the cursor; a Matter with no
       open task, or a reader who may not write, renders no box and the zone
       itself is focused through its `tabindex="-1"`. */
    target.scrollIntoView({ block: "center", behavior: "auto" });
    focusQuietly(target.querySelector("textarea") || target);
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
    /* `L` for «lisa»: the box where something gets written down. On a Matter
       with a current task that is `Mida tegid?`; on one without, there is
       nothing to complete, so it opens `+ Märge` instead. It used to open the
       composer, which was both of those and is gone (docs/adr/0075 §3). */
    var box = document.querySelector("#praegune-tegevus textarea");
    if (box) {
      event.preventDefault();
      box.focus();
      return;
    }
    var note = document.getElementById("lisa-marge");
    if (!note) {
      return;
    }
    event.preventDefault();
    openComposer(note);
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

  /* ---- The Järgmiseks row opens the composer -----------------------------
   * The row says what is owed and the box below it is where the answer is
   * written, so reaching one from the other should not need aim: a click
   * anywhere on the row toggles the composer, and opening it focuses the first
   * textarea.
   *
   * Clicks on a button, a link or a form inside the row do nothing here. Those
   * are «✓ Tehtud» and «Muuda», which have their own jobs — and «✓ Tehtud»
   * swapping this row while the click also collapsed the composer underneath
   * would throw away whatever somebody had typed into it (ADR 0052 §8).
   *
   * Bound per row rather than on the document, because the row is an HTMX swap
   * target: a delegated listener would survive the swap, and `once` on the new
   * element is what keeps one listener per rendering.
   */
  function bindComposerToggle(scope) {
    scope.querySelectorAll("[data-koostaja-toggle]").forEach(function (row) {
      if (!once(row, "KoostajaToggle")) {
        return;
      }
      row.addEventListener("click", function (event) {
        if (event.target.closest && event.target.closest("button, a, form, label, input, select, textarea")) {
          return;
        }
        var main = row.closest(".teemamain");
        var composer = main && main.querySelector("details.composer");
        if (!composer) {
          return;
        }
        composer.open = !composer.open;
        if (composer.open) {
          var box = composer.querySelector("textarea");
          if (box) {
            box.focus();
          }
        }
      });
    });
  }

  /* ---- Single-select chip groups inside the composer panels --------------
   * `Täpsus`, `Liik` and `Kuidas lõppes` are chips over a hidden input, which
   * is the field that is actually submitted and validated — the chip is a
   * faster way to choose a value and nothing more, exactly like the quick
   * dates above it.
   *
   * With no script the hidden input still carries whatever the server rendered:
   * `EXACT` for a precision, the first kind for an engagement, and nothing at
   * all for a closure, which is the value that means «nobody has answered».
   */
  function bindChipGroups(scope) {
    scope.querySelectorAll("[data-chipgroup]").forEach(function (group) {
      if (!once(group, "ChipGroup")) {
        return;
      }
      var name = group.getAttribute("data-chipgroup");
      var field = group.querySelector("input[name='" + name + "']");
      if (!field) {
        return;
      }
      var chips = group.querySelectorAll("[data-chipvalue]");
      var sync = function () {
        chips.forEach(function (chip) {
          var chosen = chip.getAttribute("data-chipvalue") === field.value;
          chip.classList.toggle("is-selected", chosen);
          chip.setAttribute("aria-pressed", chosen ? "true" : "false");
        });
      };
      chips.forEach(function (chip) {
        chip.addEventListener("click", function () {
          field.value = chip.getAttribute("data-chipvalue");
          field.dispatchEvent(new Event("change", { bubbles: true }));
          sync();
        });
      });
      sync();
    });
  }

  /* ---- A refused workspace save puts the cursor where the problem is -----
   * Every save on this page swaps `#teema-vaade`, and a swap leaves focus on
   * `<body>` — so a keyboard or screen-reader user who pressed `Salvesta` and
   * was refused lands at the top of the document with the explanation somewhere
   * below them. The message is announced by its `role="alert"`; this is the
   * other half, and it is what «errors move focus appropriately» means (§33).
   *
   * The first error on the swapped surface, and the control it belongs to —
   * never a control in a different form, and never anything at all when the
   * save succeeded.
   */
  function focusFirstRefusal(scope) {
    var problem = scope.querySelector ? scope.querySelector(".field__error") : null;
    if (!problem) {
      return;
    }
    var form = problem.closest("form");
    if (!form) {
      return;
    }
    var field = form.querySelector(
      "textarea:not([hidden]), select:not([hidden]), input:not([type=hidden]):not([hidden])"
    );
    if (field) {
      focusQuietly(field);
    }
  }

  /* ---- LISA TEEMALE: one panel open at a time ---------------------------
   * Opening one add-to-matter form closes whichever other one was open. The
   * zone is a *choice* of seven operations, and seven expanded panels stacked
   * down the page is the composer it replaces wearing different markup
   * (brief §11).
   *
   * Deliberately **not** `data-uxpopover`. That contract also closes on any
   * click outside the disclosure, which is right for a menu and wrong for a
   * form somebody is typing into: the browser lane caught it shutting under the
   * cursor mid-entry and taking the field with it (docs/adr/0074 §20).
   *
   * With scripting off every `<details>` still opens, closes, submits and
   * validates. What is lost is the tidiness, not the capability.
   */
  function bindAddPanels(scope) {
    scope.querySelectorAll("details[data-addpanel]").forEach(function (panel) {
      if (!once(panel, "AddPanel")) {
        return;
      }
      panel.addEventListener("toggle", function () {
        if (!panel.open) {
          return;
        }
        document.querySelectorAll("details[data-addpanel][open]").forEach(function (other) {
          if (other !== panel) {
            other.open = false;
          }
        });
      });
    });
  }

  /* ---- The file affordance -----------------------------------------------
   * The dashed box is a `<label>` over a hidden file input, so choosing a file
   * works with no script at all. This adds the two things a script can add:
   * dragging a file onto it, and saying which file was chosen.
   *
   * The prompt is restored on an empty selection, so clearing the picker does
   * not leave the box claiming a file that is no longer attached.
   */
  function bindFileDrop(scope) {
    scope.querySelectorAll("[data-filedrop]").forEach(function (drop) {
      if (!once(drop, "FileDrop")) {
        return;
      }
      var field = drop.querySelector("input[type=file]");
      var text = drop.querySelector("[data-filedrop-text]");
      if (!field || !text) {
        return;
      }
      var prompt = text.innerHTML;
      /* One name, or how many. Every workspace control now takes several files,
         and listing eight filenames inside a dashed box is a box nobody can
         read — the count is what somebody checks before pressing Salvesta
         (brief §21). */
      var show = function () {
        var files = field.files;
        var count = files ? files.length : 0;
        drop.classList.toggle("is-chosen", count > 0);
        if (count === 1) {
          text.textContent = files[0].name;
        } else if (count > 1) {
          text.textContent = count + " faili";
        } else {
          text.innerHTML = prompt;
        }
      };
      field.addEventListener("change", show);
      ["dragenter", "dragover"].forEach(function (name) {
        drop.addEventListener(name, function (event) {
          event.preventDefault();
          drop.classList.add("is-dragover");
        });
      });
      ["dragleave", "drop"].forEach(function (name) {
        drop.addEventListener(name, function () {
          drop.classList.remove("is-dragover");
        });
      });
      drop.addEventListener("drop", function (event) {
        if (!event.dataTransfer || !event.dataTransfer.files.length) {
          return;
        }
        event.preventDefault();
        field.files = event.dataTransfer.files;
        show();
      });
      show();
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
    bindComposerToggle(root);
    bindChipGroups(root);
    bindAddPanels(root);
    bindFileDrop(root);
    bindWorkRows(root);
    bindExclusivePopovers(root);
    bindCopyLink(root);
    bindRowFilter(root);
  }

  document.addEventListener("DOMContentLoaded", function () {
    bindAll(document);
  });

  /* Once, on the way in — and deliberately not inside `bindAll`, which also
     runs after every HTMX swap. Re-opening the composer on each swap would
     reopen a box the reader had just closed. See the note above for why this
     waits for `load` rather than joining the handler above. */
  window.addEventListener("load", arriveAtNextStep);

  document.body.addEventListener("htmx:afterSwap", function (event) {
    bindAll(event.target);
    focusFirstRefusal(event.target);
  });
})();
