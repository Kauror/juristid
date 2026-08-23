/*
 * Small behaviours only. Everything else is server-rendered HTML and HTMX.
 *
 * The rule this file follows: nothing here is the only way to do anything.
 * Every shortcut has a visible control, every disclosure is driven by a real
 * form control, and the page works with JavaScript disabled — the optional
 * composer fields simply start visible instead of hidden
 * (master specification 17.7).
 */
(function () {
  "use strict";

  /* ---- Ctrl/Cmd+K focuses the global search ------------------------------
   * Focus, not a command palette. The field is already visible on every page;
   * the shortcut saves a reach for the mouse and nothing more.
   */
  document.addEventListener("keydown", function (event) {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      var field = document.getElementById("global-search");
      if (field) {
        event.preventDefault();
        field.focus();
        field.select();
      }
    }
  });

  /* ---- Uus teema: the "Muu" valdkond reveals its own text field --------
   * Progressive enhancement only. Without JavaScript the input is visible from
   * the start and the form still works — the server decides what "Muu" means,
   * not this (Stage-2E.1 brief 20).
   */
  var otherArea = document.querySelector("#valdkond-muu input[type=checkbox]");
  var otherAreaText = document.getElementById("valdkond-muu-tekst");
  if (otherArea && otherAreaText) {
    var syncOtherArea = function () {
      otherAreaText.hidden = !otherArea.checked;
      if (otherArea.checked) {
        var input = otherAreaText.querySelector("input");
        if (input) {
          input.focus();
        }
      }
    };
    otherArea.addEventListener("change", syncOtherArea);
    syncOtherArea();
  }

  /* ---- Uus teema: say which files are about to be uploaded --------------
   * A file input shows "3 files" and nothing about which three. This lists
   * them before saving, so a wrong pick is visible while it is still cheap.
   */
  var fileInput = document.getElementById("id_files");
  var fileList = document.getElementById("valitud-failid");
  if (fileInput && fileList) {
    fileInput.addEventListener("change", function () {
      fileList.textContent = "";
      var chosen = Array.prototype.slice.call(fileInput.files || []);
      fileList.hidden = chosen.length === 0;
      chosen.forEach(function (file) {
        var item = document.createElement("li");
        item.className = "dropzone__file";
        item.textContent = file.name;
        fileList.appendChild(item);
      });
    });

    var zone = fileInput.closest(".dropzone");
    if (zone) {
      ["dragenter", "dragover"].forEach(function (name) {
        zone.addEventListener(name, function (event) {
          event.preventDefault();
          zone.classList.add("is-over");
        });
      });
      ["dragleave", "drop"].forEach(function (name) {
        zone.addEventListener(name, function (event) {
          event.preventDefault();
          zone.classList.remove("is-over");
        });
      });
      zone.addEventListener("drop", function (event) {
        if (event.dataTransfer && event.dataTransfer.files.length) {
          fileInput.files = event.dataTransfer.files;
          fileInput.dispatchEvent(new Event("change"));
        }
      });
    }
  }

  /* ---- Composer: Ctrl/Cmd+Enter submits, Esc closes optional fields ------ */
  document.addEventListener("keydown", function (event) {
    var composer = event.target.closest ? event.target.closest("form[data-composer]") : null;
    if (!composer) {
      return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      composer.requestSubmit();
      return;
    }
    if (event.key === "Escape") {
      composer.querySelectorAll("[data-reveals]").forEach(function (trigger) {
        var target = document.getElementById(trigger.getAttribute("data-reveals"));
        if (target && !target.hidden) {
          target.hidden = true;
          trigger.classList.remove("is-active");
        }
      });
    }
  });

  /* ---- Progressive disclosure --------------------------------------------
   * Two kinds. A "reveals" button shows an optional block; a "toggles"
   * checkbox does the same but its state is submitted with the form, which is
   * what lets one save carry both an entry and a Järgmiseks change.
   */
  function syncCheckbox(toggle) {
    var target = document.getElementById(toggle.getAttribute("data-toggles"));
    if (target) {
      target.hidden = !toggle.checked;
    }
    var chip = toggle.closest("[data-toggle-chip]");
    if (chip) {
      chip.classList.toggle("is-active", toggle.checked);
    }
  }

  /* HTMX swaps whole surfaces and `htmx:afterSwap` fires for every one of them,
     so a listener attached without a guard is attached again for any element
     that survives a swap of its container — and then a single click toggles a
     disclosure twice and it looks like nothing happened. The flag is on the
     element, so it travels with it and dies with it. */
  function once(element, name) {
    var key = "bound" + name;
    if (element.dataset[key]) {
      return false;
    }
    element.dataset[key] = "1";
    return true;
  }

  function bind(root) {
    var scope = root || document;

    scope.querySelectorAll("[data-toggles]").forEach(function (toggle) {
      syncCheckbox(toggle);
      if (!once(toggle, "Toggle")) {
        return;
      }
      toggle.addEventListener("change", function () {
        syncCheckbox(toggle);
      });
    });

    scope.querySelectorAll("[data-reveals]").forEach(function (trigger) {
      if (!once(trigger, "Reveal")) {
        return;
      }
      trigger.addEventListener("click", function () {
        var target = document.getElementById(trigger.getAttribute("data-reveals"));
        if (!target) {
          return;
        }
        target.hidden = !target.hidden;
        trigger.classList.toggle("is-active", !target.hidden);
        if (!target.hidden) {
          var first = target.querySelector("input, select, textarea");
          if (first) {
            first.focus();
          }
        }
      });
    });

    /* An inline header edit commits on change; the visible Salvesta button
       remains for keyboard users and for anyone with JS disabled. */
    scope.querySelectorAll("[data-autosubmit]").forEach(function (control) {
      if (!once(control, "Autosubmit")) {
        return;
      }
      control.addEventListener("change", function () {
        if (control.form) {
          control.form.requestSubmit();
        }
      });
    });
  }

  /* ---- Menus close the way people expect --------------------------------
   * A <details> menu stays open until its own summary is clicked again, which
   * is right for a disclosure inside a page and wrong for one that floats over
   * it. Delegated, so it costs nothing per menu and survives every swap.
   */
  document.addEventListener("click", function (event) {
    document.querySelectorAll("details.topnav__more[open]").forEach(function (menu) {
      if (!menu.contains(event.target)) {
        menu.open = false;
      }
    });
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") {
      return;
    }
    var open = document.querySelector("details.topnav__more[open]");
    if (open) {
      open.open = false;
      var trigger = open.querySelector("summary");
      if (trigger) {
        trigger.focus();
      }
    }
  });

  /* ---- The period control: show only the fields the precision needs -------
   * Progressive enhancement only. With scripting off every group is visible,
   * every one is optional, and the server decides which of them it needs — so
   * the form still works and still refuses an impossible combination
   * (app/intelligence/forms.py, Stage-2G brief 7, 49).
   */
  function bindPeriodFields(scope) {
    if (!scope || !scope.querySelector) {
      return;
    }
    var fields = scope.querySelector("#perioodi-valjad");
    var chooser = scope.querySelector("#tapsuse-valik");
    if (!fields || !chooser) {
      return;
    }
    /* Jõustumine asks a question before the precision one: a commencement that
     * happens "üldises korras" has no date to be precise about, so the whole
     * control goes away rather than sitting there inviting a fabricated day. */
    var kindChooser = scope.querySelector("#joustumise-liik");
    var groups = Array.prototype.slice.call(
      fields.querySelectorAll(".periodfields__group")
    );
    var sync = function () {
      var chosen = chooser.querySelector("input:checked");
      var value = chosen ? chosen.value : "";
      if (kindChooser) {
        var kind = kindChooser.querySelector("input:checked");
        var dated = kind ? kind.value === "KNOWN_DATE" : true;
        chooser.hidden = !dated;
        chooser.classList.toggle("is-hidden", !dated);
        fields.hidden = !dated;
        fields.classList.toggle("is-hidden", !dated);
        if (!dated) {
          return;
        }
      }
      groups.forEach(function (group) {
        var applicable = (group.getAttribute("data-precision") || "").split(" ");
        /* `hidden` alone loses to any component rule that sets `display`, which
         * is how the composer's disclosure was broken once already — so the
         * class carries the rule and `hidden` carries the semantics. */
        var show = value !== "" && applicable.indexOf(value) !== -1;
        group.hidden = !show;
        group.classList.toggle("is-hidden", !show);
      });
    };
    chooser.querySelectorAll("input[type=radio]").forEach(function (radio) {
      if (once(radio, "Precision")) {
        radio.addEventListener("change", sync);
      }
    });
    if (kindChooser) {
      kindChooser.querySelectorAll("input[type=radio]").forEach(function (radio) {
        if (once(radio, "Kind")) {
          radio.addEventListener("change", sync);
        }
      });
    }
    sync();
  }

  /* ---- The Estonian date control ----------------------------------------
   * Every date box in the application is a text input reading 7.9.2026,
   * because a native date input renders in the *browser's* locale: a lawyer on
   * a US-English Windows saw mm/dd/yyyy on an otherwise Estonian form, with no
   * way to know it would read 7.9.2026 as the 9th of July (app/core/dates.py).
   *
   * This adds back the calendar that control gave up. Weeks start on Monday,
   * the headings and month names are Estonian, and it is progressive
   * enhancement throughout: with scripting off the box is still a text field
   * the server parses, which is what a keyboard user was typing into anyway.
   */
  var WEEKDAYS = ["E", "T", "K", "N", "R", "L", "P"];
  var WEEKDAY_NAMES = [
    "esmaspäev",
    "teisipäev",
    "kolmapäev",
    "neljapäev",
    "reede",
    "laupäev",
    "pühapäev",
  ];
  var MONTHS = [
    "jaanuar", "veebruar", "märts", "aprill", "mai", "juuni",
    "juuli", "august", "september", "oktoober", "november", "detsember",
  ];

  function formatEstonian(date) {
    return date.getDate() + "." + (date.getMonth() + 1) + "." + date.getFullYear();
  }

  /* Mirrors app/core/dates.parse_estonian_date, including its refusal to
     approximate: 31.02 is somebody mistyping, and the 28th is not what they
     meant. ISO is accepted for the same reason the server accepts it — links
     written before this control carry it. */
  function parseEstonian(text) {
    var value = (text || "").trim();
    var parts = /^(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})$/.exec(value);
    var year, month, day;
    if (parts) {
      day = parseInt(parts[1], 10);
      month = parseInt(parts[2], 10);
      year = parseInt(parts[3], 10);
      if (parts[3].length === 2) {
        year += 2000;
      }
    } else {
      var iso = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
      if (!iso) {
        return null;
      }
      year = parseInt(iso[1], 10);
      month = parseInt(iso[2], 10);
      day = parseInt(iso[3], 10);
    }
    var candidate = new Date(year, month - 1, day);
    if (
      candidate.getFullYear() !== year ||
      candidate.getMonth() !== month - 1 ||
      candidate.getDate() !== day
    ) {
      return null;
    }
    return candidate;
  }

  function sameDay(a, b) {
    return (
      a.getFullYear() === b.getFullYear() &&
      a.getMonth() === b.getMonth() &&
      a.getDate() === b.getDate()
    );
  }

  /* Monday is 0 here. getDay() calls Sunday 0, which is the American week and
     would put every date in the grid one column out. */
  function mondayIndex(date) {
    return (date.getDay() + 6) % 7;
  }

  function closePicker(panel) {
    panel.hidden = true;
    var trigger = panel.parentNode
      ? panel.parentNode.querySelector(".datepicker__trigger")
      : null;
    if (trigger) {
      trigger.setAttribute("aria-expanded", "false");
    }
  }

  function buildCalendar(panel, input, visible) {
    panel.textContent = "";

    var head = document.createElement("div");
    head.className = "datepicker__head";

    var previous = document.createElement("button");
    previous.type = "button";
    previous.className = "datepicker__nav";
    previous.textContent = "‹";
    previous.setAttribute("aria-label", "Eelmine kuu");

    var title = document.createElement("span");
    title.className = "datepicker__title";
    title.setAttribute("aria-live", "polite");
    title.textContent = MONTHS[visible.getMonth()] + " " + visible.getFullYear();

    var next = document.createElement("button");
    next.type = "button";
    next.className = "datepicker__nav";
    next.textContent = "›";
    next.setAttribute("aria-label", "Järgmine kuu");

    previous.addEventListener("click", function () {
      buildCalendar(panel, input, new Date(visible.getFullYear(), visible.getMonth() - 1, 1));
    });
    next.addEventListener("click", function () {
      buildCalendar(panel, input, new Date(visible.getFullYear(), visible.getMonth() + 1, 1));
    });

    head.appendChild(previous);
    head.appendChild(title);
    head.appendChild(next);
    panel.appendChild(head);

    var grid = document.createElement("div");
    grid.className = "datepicker__grid";

    WEEKDAYS.forEach(function (short, index) {
      var cell = document.createElement("span");
      cell.className = "datepicker__weekday";
      /* The single letter is what fits a seven-column grid; the full weekday is
         what a screen reader should say. Both, rather than one chosen for
         everybody. */
      cell.setAttribute("aria-label", WEEKDAY_NAMES[index]);
      cell.title = WEEKDAY_NAMES[index];
      cell.textContent = short;
      grid.appendChild(cell);
    });

    var first = new Date(visible.getFullYear(), visible.getMonth(), 1);
    var lead = mondayIndex(first);
    var days = new Date(visible.getFullYear(), visible.getMonth() + 1, 0).getDate();
    var selected = parseEstonian(input.value);
    var today = new Date();

    for (var blank = 0; blank < lead; blank += 1) {
      var filler = document.createElement("span");
      filler.className = "datepicker__blank";
      grid.appendChild(filler);
    }

    var choose = function (chosen) {
      return function () {
        input.value = formatEstonian(chosen);
        /* Both events, and in this order. `input` is what a live filter
           listens for; `change` is what data-autosubmit commits on. A control
           that set .value silently would look like it worked and save
           nothing. */
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.dispatchEvent(new Event("change", { bubbles: true }));
        closePicker(panel);
        input.focus();
      };
    };

    for (var day = 1; day <= days; day += 1) {
      var date = new Date(visible.getFullYear(), visible.getMonth(), day);
      var button = document.createElement("button");
      button.type = "button";
      button.className = "datepicker__day";
      button.textContent = String(day);
      button.setAttribute("aria-label", formatEstonian(date));
      if (sameDay(date, today)) {
        button.classList.add("is-today");
      }
      if (selected && sameDay(date, selected)) {
        button.classList.add("is-selected");
        button.setAttribute("aria-current", "date");
      }
      button.addEventListener("click", choose(date));
      grid.appendChild(button);
    }

    panel.appendChild(grid);
  }

  function bindDatePickers(scope) {
    (scope || document).querySelectorAll("input[data-datepicker]").forEach(function (input) {
      if (!once(input, "Datepicker")) {
        return;
      }
      var wrap = document.createElement("span");
      wrap.className = "datepicker";
      input.parentNode.insertBefore(wrap, input);
      wrap.appendChild(input);

      var trigger = document.createElement("button");
      trigger.type = "button";
      trigger.className = "datepicker__trigger";
      trigger.setAttribute("aria-expanded", "false");
      trigger.setAttribute("aria-label", "Ava kalender");
      trigger.textContent = "📅";
      wrap.appendChild(trigger);

      var panel = document.createElement("div");
      panel.className = "datepicker__panel";
      panel.hidden = true;
      wrap.appendChild(panel);

      trigger.addEventListener("click", function () {
        if (panel.hidden) {
          buildCalendar(panel, input, parseEstonian(input.value) || new Date());
          panel.hidden = false;
          trigger.setAttribute("aria-expanded", "true");
        } else {
          closePicker(panel);
        }
      });

      /* Escape closes it and returns focus to the box, and a click anywhere
         else closes it too — a floating panel that stays open until its own
         button is clicked again is the disclosure people report as stuck. */
      wrap.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && !panel.hidden) {
          closePicker(panel);
          input.focus();
        }
      });
      document.addEventListener("click", function (event) {
        if (!panel.hidden && !wrap.contains(event.target)) {
          closePicker(panel);
        }
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    bind(document);
    bindPeriodFields(document);
    bindDatePickers(document);
  });

  /* A rejected save returns 400 with the surface re-rendered and the errors in
   * place. HTMX drops non-2xx responses unless told otherwise, which would make
   * a validation failure look like nothing happened at all. */
  document.body.addEventListener("htmx:beforeSwap", function (event) {
    if (event.detail.xhr && event.detail.xhr.status === 400) {
      event.detail.shouldSwap = true;
      event.detail.isError = false;
    }
  });

  /* HTMX replaces whole surfaces, so re-bind inside whatever just arrived.
     Binding is idempotent, so a swap that returns elements which were already
     bound costs nothing and duplicates nothing. */
  document.body.addEventListener("htmx:afterSwap", function (event) {
    bind(event.target);
    bindPeriodFields(event.target.querySelector ? event.target : document);
    bindDatePickers(event.target.querySelector ? event.target : document);
  });
})();
