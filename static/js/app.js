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
  /* ---- A refused save must say why --------------------------------------
   * Every 400 this application returns from an HTMX endpoint carries the
   * re-rendered surface with the reason on it: the composer with its field
   * error and the text still in the box, the engagement form with what was
   * typed. htmx 2 does not swap 4xx by default, which means the server
   * explains itself and the page silently discards the explanation — somebody
   * presses Salvesta and nothing whatsoever happens.
   *
   * Only 400 and 422. A 404 is the authorization answer this application gives
   * for a record somebody may not touch, and swapping Django's error page into
   * a fragment target would be worse than ignoring it.
   *
   * `defer` on both scripts, htmx first, so the global is here.
   */
  if (window.htmx && window.htmx.config) {
    window.htmx.config.responseHandling = [
      { code: "204", swap: false },
      { code: "[23]..", swap: true },
      { code: "4(00|22)", swap: true, error: true },
      { code: "[45]..", swap: false, error: true },
    ];
  }

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
   * them before saving, with the size and a way to take one back off, so a
   * wrong pick is visible and reversible while it is still free — after the
   * save each of these is an immutable version and removing it is a different
   * kind of act (app/documents, Uus teema redesign §9).
   */
  function humanSize(bytes) {
    if (bytes < 1024) {
      return bytes + " B";
    }
    if (bytes < 1024 * 1024) {
      return Math.round(bytes / 1024) + " KB";
    }
    return (bytes / (1024 * 1024)).toFixed(1).replace(".", ",") + " MB";
  }

  var fileInput = document.getElementById("id_files");
  var fileList = document.getElementById("valitud-failid");
  if (fileInput && fileList) {
    var withoutIndex = function (skip) {
      /* A FileList is read-only, so the way to drop one file is to build a new
         transfer holding the others. Supported everywhere this application
         runs; where it is not, the button simply does not appear. */
      var transfer = new DataTransfer();
      Array.prototype.slice.call(fileInput.files || []).forEach(function (file, index) {
        if (index !== skip) {
          transfer.items.add(file);
        }
      });
      fileInput.files = transfer.files;
      fileInput.dispatchEvent(new Event("change"));
    };
    var canDrop = typeof DataTransfer === "function";

    fileInput.addEventListener("change", function () {
      fileList.textContent = "";
      var chosen = Array.prototype.slice.call(fileInput.files || []);
      fileList.hidden = chosen.length === 0;
      chosen.forEach(function (file, index) {
        var item = document.createElement("li");
        item.className = "dropzone__file";

        var kind = document.createElement("span");
        kind.className = "dropzone__kind";
        kind.textContent = "TÕEND";
        item.appendChild(kind);

        var name = document.createElement("span");
        name.className = "dropzone__name";
        name.textContent = file.name;
        item.appendChild(name);

        var size = document.createElement("span");
        size.className = "dropzone__size";
        size.textContent = humanSize(file.size);
        item.appendChild(size);

        if (canDrop) {
          var drop = document.createElement("button");
          drop.type = "button";
          drop.className = "dropzone__drop";
          drop.textContent = "×";
          drop.setAttribute("aria-label", "Eemalda fail " + file.name);
          drop.addEventListener("click", function () {
            withoutIndex(index);
          });
          item.appendChild(drop);
        }

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

  /* ---- Inline editors: Ctrl/Cmd+Enter saves, Esc cancels -----------------
   * Every edit in the Matter workflow happens where the value is shown, inside
   * a <details> that opened in place. The two keys behave the same in all of
   * them — the summary, the position, an engagement — because a shortcut that
   * works in one box and not the next is a shortcut nobody trusts. Both have a
   * visible click equivalent beside them (master specification 22.3).
   *
   * Delegated, so it costs nothing per editor and survives every HTMX swap.
   */
  document.addEventListener("keydown", function (event) {
    if (!event.target.closest) {
      return;
    }
    var form = event.target.closest("details form");
    if (!form || event.target.closest("form[data-composer]")) {
      return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      form.requestSubmit();
      return;
    }
    if (event.key === "Escape") {
      var holder = form.closest("details");
      if (holder && holder.open) {
        event.preventDefault();
        holder.open = false;
        var trigger = holder.querySelector("summary");
        if (trigger) {
          trigger.focus();
        }
      }
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
      /* A panel the server rendered open — a refused closure comes back that
         way — must find its chip already active, or the first click on it
         would close the section holding the error. */
      var revealed = document.getElementById(trigger.getAttribute("data-reveals"));
      trigger.classList.toggle("is-active", !!revealed && !revealed.hidden);
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

    /* "Muuda" and "Määra allpool ↓" on the Järgmiseks row send the reader to
       the composer rather than opening a second editor for the same value.
       There is exactly one place a next step is written, and a competing inline
       form would be a competing implementation of the same domain call
       (Teema redesign §26.3). */
    scope.querySelectorAll("[data-focus]").forEach(function (trigger) {
      if (!once(trigger, "Focus")) {
        return;
      }
      trigger.addEventListener("click", function () {
        var target = document.getElementById(trigger.getAttribute("data-focus"));
        if (!target) {
          return;
        }
        /* `prefers-reduced-motion` is honoured by asking for "auto", which the
           browser resolves against the user's own setting. */
        target.scrollIntoView({ block: "center", behavior: "auto" });
        /* Not `input` in general: every form here opens with a hidden CSRF
           token, and it is the first match in document order. */
        var box = target.querySelector(
          "textarea, select, input:not([type=hidden])"
        );
        if (box) {
          box.focus();
        }
      });
    });

    /* Kaasamine: the explicit add action opens the form, not just the section.

       `+ Lisa` sits inside the accordion's own <summary>, where a plain span is
       nothing but the disclosure's toggle: it opened the section and left the
       composer shut, so the one control that says "add" still needed a second
       click before anything could be added (Kaasamine one-click §13).

       The empty state needs none of this. With no records the server renders
       the form directly in the section body, so a single click on the header is
       already the whole gesture — and it is the whole gesture with JavaScript
       switched off too, which is why the fix is not a script that opens things
       on toggle (Kaasamine one-click §5, §11). */
    scope.querySelectorAll("[data-engagement-add-trigger]").forEach(function (trigger) {
      if (!once(trigger, "EngagementAdd")) {
        return;
      }
      trigger.addEventListener("click", function (event) {
        var section = trigger.closest("details.accordion");
        if (!section) {
          return;
        }
        /* Both, and both are load-bearing. Without `preventDefault` the
           summary's own activation runs after this listener and shuts the
           section this just opened; without `stopPropagation` the document-level
           listeners on the way up see a click that is not theirs. */
        event.preventDefault();
        event.stopPropagation();
        section.open = true;
        var composer = section.querySelector("[data-engagement-composer]");
        if (composer) {
          composer.open = true;
        }
        var form = section.querySelector("form[data-engagement-add]");
        if (!form) {
          return;
        }
        /* An explicit Add may take the focus; opening the section may not
           (Kaasamine one-click §14). Not `input` in general: every form here
           opens with a hidden CSRF token, and it is first in document order. */
        var field = form.querySelector("select, textarea, input:not([type=hidden])");
        if (field) {
          field.focus();
        }
      });
    });

    /* The composer's primary button says what the save will actually do. A
       button reading "Salvesta" that closes the file is the one thing a
       destructive-ish action must never look like (Teema redesign §15). */
    scope.querySelectorAll("[data-closes-matter]").forEach(function (toggle) {
      if (!once(toggle, "Closes")) {
        return;
      }
      var form = toggle.form;
      var submit = form ? form.querySelector("[data-composer-submit]") : null;
      if (!submit) {
        return;
      }
      var sync = function () {
        submit.textContent = toggle.checked
          ? submit.getAttribute("data-label-closing")
          : submit.getAttribute("data-label-default");
        submit.classList.toggle("button--danger", toggle.checked);
      };
      toggle.addEventListener("change", sync);
      sync();
    });

    /* SAAJA — `Muu`, as many times as the letter needs.
       A closing opinion can go to seven bodies none of which are in the
       catalogue yet, and creating them one page-load at a time is not a
       workflow anybody would use. Each added name becomes a chip carrying its
       own hidden input under the same field name, so the server sees a list
       however many there are.

       The visible box carries that name too, which is what keeps the control
       honest with no script running: type one recipient, save, done. When this
       binds, adding moves the value into a chip and empties the box, so the
       box never contributes the name twice
       (app/matters/forms.py MultiTextInput, Teema closing redesign §7B). */
    scope.querySelectorAll("[data-recipients]").forEach(function (holder) {
      if (!once(holder, "Recipients")) {
        return;
      }
      var box = holder.querySelector("[data-recipient-input]");
      var list = holder.querySelector("[data-recipient-list]");
      var add = holder.querySelector("[data-recipient-add]");
      if (!box || !list || !add) {
        return;
      }

      var chosen = function () {
        return Array.prototype.map.call(
          list.querySelectorAll("input[type=hidden]"),
          function (input) {
            return input.value.toLowerCase();
          }
        );
      };

      var remove = function (event) {
        var button = event.target.closest("[data-recipient-remove]");
        if (button) {
          button.closest(".recipientadd__item").remove();
        }
      };

      var append = function () {
        var name = box.value.trim().replace(/\s+/g, " ");
        box.value = "";
        box.focus();
        if (!name || chosen().indexOf(name.toLowerCase()) !== -1) {
          /* The same body twice is one recipient, which is what the form, the
             service and the unique recipient-per-submission constraint all
             say. Saying it here too is what keeps the count on screen equal to
             the count that is stored (§7F). */
          return;
        }
        var item = document.createElement("li");
        item.className = "recipientadd__item";
        var label = document.createElement("span");
        label.className = "recipientadd__name";
        label.textContent = name;
        var hidden = document.createElement("input");
        hidden.type = "hidden";
        hidden.name = box.name;
        hidden.value = name;
        var drop = document.createElement("button");
        drop.type = "button";
        drop.className = "recipientadd__remove";
        drop.setAttribute("data-recipient-remove", "");
        drop.setAttribute("aria-label", "Eemalda saaja " + name);
        drop.textContent = "×";
        item.appendChild(label);
        item.appendChild(hidden);
        item.appendChild(drop);
        list.appendChild(item);
      };

      add.addEventListener("click", append);
      list.addEventListener("click", remove);
      box.addEventListener("keydown", function (event) {
        /* Enter adds the recipient rather than submitting the composer, which
           is what somebody halfway through a list of seven means by it. */
        if (event.key === "Enter") {
          event.preventDefault();
          append();
        }
      });
    });

    /* TÖÖVÕIT — the commencement date belongs to "Jah" and to nothing else.
       Hidden by markup on the server, so a refused save that said Jah comes
       back with the box open and its error visible; this only follows the
       radios while somebody is filling the form in. */
    scope.querySelectorAll(".composer [data-victory-date]").forEach(function (panel) {
      if (!once(panel, "VictoryDate")) {
        return;
      }
      var form = panel.closest("form");
      if (!form) {
        return;
      }
      var radios = form.querySelectorAll("input[name=work_victory]");
      var sync = function () {
        var chosen = form.querySelector("input[name=work_victory]:checked");
        panel.hidden = !chosen || chosen.value !== "JAH";
      };
      radios.forEach(function (radio) {
        radio.addEventListener("change", sync);
      });
      sync();
    });

    /* The private note saves itself and says so. `beforeunload` covers the one
       case the debounce cannot: somebody types a line and closes the tab
       inside the delay window. */
    scope.querySelectorAll(".railnote").forEach(function (form) {
      if (!once(form, "Note")) {
        return;
      }
      var box = form.querySelector("textarea");
      if (!box) {
        return;
      }
      var saved = box.value;
      window.addEventListener("beforeunload", function () {
        if (box.value === saved) {
          return;
        }
        var body = new FormData(form);
        var url = form.getAttribute("hx-post");
        if (url && navigator.sendBeacon) {
          navigator.sendBeacon(url, body);
        }
      });
      form.addEventListener("htmx:afterRequest", function () {
        saved = box.value;
      });
    });
  }

  /* ---- The Arvamused section keeps the register's state ------------------
   * Teemad carries two independent searches: `?q=` narrows teemad and
   * `?arvamus_q=` narrows the Arvamused section under them (docs/adr/0047).
   * Both live in one address, and neither may reset the other.
   *
   * The register's live search swaps `#teemad-tulemused` and pushes a new
   * address. The Arvamused section sits *outside* that region, so everything
   * the server baked into it — the tab hrefs, and the hidden inputs the opinion
   * form carries the register's state in — still describes the address the page
   * was *rendered* with. Two things then go wrong, and the second is the worse
   * one:
   *
   *   - following a stale tab href navigates to the old `?q=` and silently
   *     undoes the teemad search somebody just typed;
   *   - a stale hidden `q` is sent with the opinion search, and the server
   *     composes `HX-Push-Url` from it — writing the old register state over
   *     the correct address bar, which is worse than not pushing at all.
   *
   * So the section is resynced from what is actually true — `location.search`
   * for the register's state — whenever htmx pushes a new address, and again at
   * click time for a tab, which also folds in whatever is in the opinion box
   * right now.
   *
   * Nothing here is the only way to do anything. With JavaScript off there is
   * no live search for anything to go stale from, so the server-rendered markup
   * is already right and none of this runs.
   */
  var OPINION_PARAMS = ["arvamus_q", "arvamus_vaade"];

  function opinionForm() {
    var box = document.getElementById("arvamused-otsing");
    return box ? box.form : null;
  }

  /* The register's half of the current address, as name/value pairs. */
  function registerState() {
    var pairs = [];
    new URL(window.location.href).searchParams.forEach(function (value, name) {
      if (OPINION_PARAMS.indexOf(name) === -1 && value) {
        pairs.push([name, value]);
      }
    });
    return pairs;
  }

  /* Rewrite the opinion form's carried register inputs to match the address.
   *
   * The opinion form's own controls are left alone: `arvamus_q` is what
   * somebody is typing into and `arvamus_vaade` is which tab they are on, and
   * neither is the register's to set.
   */
  function syncOpinionForm() {
    var form = opinionForm();
    if (!form) {
      return;
    }
    form.querySelectorAll("input[type=hidden][data-register-state]").forEach(function (input) {
      input.remove();
    });
    registerState().forEach(function (pair) {
      var input = document.createElement("input");
      input.type = "hidden";
      input.name = pair[0];
      input.value = pair[1];
      input.setAttribute("data-register-state", "");
      form.appendChild(input);
    });
  }

  document.addEventListener("htmx:pushedIntoHistory", syncOpinionForm);

  document.addEventListener("click", function (event) {
    var tab = event.target.closest ? event.target.closest("[data-opinion-tab]") : null;
    if (!tab || event.defaultPrevented || event.metaKey || event.ctrlKey || event.shiftKey) {
      return;
    }
    var address = new URL(window.location.href);
    address.hash = "arvamused";
    address.searchParams.set("arvamus_vaade", tab.getAttribute("data-opinion-tab"));

    var box = document.getElementById("arvamused-otsing");
    if (box && box.value) {
      address.searchParams.set("arvamus_q", box.value);
    } else if (box) {
      address.searchParams.delete("arvamus_q");
    }
    tab.setAttribute("href", address.pathname + address.search + "#arvamused");
  });

  /* ---- Menus close the way people expect --------------------------------
   * A <details> menu stays open until its own summary is clicked again, which
   * is right for a disclosure inside a page and wrong for one that floats over
   * it. Delegated, so it costs nothing per menu and survives every swap.
   */
  document.addEventListener("click", function (event) {
    document.querySelectorAll("details.topnav__more[open], details.headmenu[open]").forEach(function (menu) {
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
    if (!scope || !scope.querySelectorAll) {
      return;
    }
    /* Every precision control on the page, not one addressed by id. The
     * composer carries two of them at once — the next step's date and an
     * important deadline's — so the pairing is structural: a `.periodfields`
     * block belongs to the `.choiceset` that precedes it under the same
     * parent. */
    scope.querySelectorAll(".periodfields").forEach(function (fields) {
      /* The *nearest preceding* `.choiceset`, walked backwards. Jõustumine has
         two of them in one parent — "what is known about the date" comes
         before "how exact is it" — and taking the first match paired the
         period groups with the wrong question, which hid the date field
         entirely and made the form unusable. */
      var chooser = fields.previousElementSibling;
      while (chooser && !chooser.classList.contains("choiceset")) {
        chooser = chooser.previousElementSibling;
      }
      if (chooser) {
        bindOnePeriodControl(scope, fields, chooser);
      }
    });
  }

  function bindOnePeriodControl(scope, fields, chooser) {
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

    /* `stopPropagation`, and it is load-bearing rather than defensive.
     *
     * `buildCalendar` starts by emptying the panel, which detaches the very
     * button that was clicked. The click then carries on to the document, where
     * the outside-close check asks whether the wrapper contains `event.target`
     * — and a detached node is contained by nothing, so the answer was false
     * and the panel closed the instant it had been rebuilt. Somebody clicking
     * "next month" saw the calendar vanish.
     *
     * Keeping the event inside the calendar is the honest fix: navigating a
     * month is not a click outside the calendar and should never have been
     * offered to a listener whose job is to notice one. The document listener
     * is hardened separately, so neither depends on the other. */
    previous.addEventListener("click", function (event) {
      event.stopPropagation();
      buildCalendar(panel, input, new Date(visible.getFullYear(), visible.getMonth() - 1, 1));
    });
    next.addEventListener("click", function (event) {
      event.stopPropagation();
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
    });
  }

  /* One outside-close for every date picker on the page, not one per input.
   *
   * Two reasons. Each picker used to add its own document listener, so a page
   * that swapped in date fields several times accumulated a listener per field
   * ever rendered, most of them holding a detached panel.
   *
   * And containment is read from the event's composed path rather than from the
   * live tree. The path is captured when the event is dispatched, so it still
   * names the calendar even if the handler that ran first has since replaced
   * the node that was clicked — which is exactly what month navigation does.
   * `contains()` is the fallback for anything that does not implement it. */
  function clickedInside(event, element) {
    if (typeof event.composedPath === "function") {
      return event.composedPath().indexOf(element) !== -1;
    }
    return element.contains(event.target);
  }

  document.addEventListener("click", function (event) {
    document.querySelectorAll(".datepicker__panel").forEach(function (panel) {
      if (panel.hidden) {
        return;
      }
      var wrap = panel.closest(".datepicker");
      if (wrap && !clickedInside(event, wrap)) {
        closePicker(panel);
      }
    });
  });

  /* ---- Narrowing a long list of chips ------------------------------------
   * One search box over one already-rendered checkbox list. No request, no
   * store: the choices are in the page and this only hides the ones that do
   * not match, which is why a ticked option that scrolls out of the filter
   * still submits — hiding a checkbox does not clear it.
   *
   * Progressive enhancement. With scripting off every choice is visible and
   * tickable, which is what the multiple select it replaced offered anyway.
   */
  function bindChoiceFilters(scope) {
    (scope || document).querySelectorAll("[data-choicefilter]").forEach(function (holder) {
      if (!once(holder, "ChoiceFilter")) {
        return;
      }
      var list = document.getElementById(holder.getAttribute("data-choicefilter"));
      var box = holder.querySelector("input");
      if (!list || !box) {
        return;
      }
      box.addEventListener("input", function () {
        var needle = box.value.trim().toLowerCase();
        /* `.chip` is Uus teema's control, `.checkitem` the one every other
           surface still uses. One selector rather than two bindings, because
           the rule — hide what does not match, never hide what is ticked — is
           the same on both. */
        list.querySelectorAll(".chip, .checkitem").forEach(function (item) {
          var name = (item.textContent || "").trim().toLowerCase();
          var checked = item.querySelector("input:checked");
          /* A ticked choice never hides. Somebody who types after choosing
             should still be able to see — and untick — what they chose. */
          item.hidden = !checked && needle !== "" && name.indexOf(needle) === -1;
        });
      });
    });
  }

  /* ---- Choosing a chip clears the name typed beside it -------------------
   * Adressaat can be answered twice on one form: by picking an institution
   * that exists, or by typing one that does not. The server resolves that with
   * a fixed rule — a typed name wins, because on `Muuda teemat` the chip group
   * always carries the addressee the Matter already has and nothing could
   * otherwise be replaced by typing.
   *
   * That rule is right and it is invisible. Somebody who types a name, changes
   * their mind and clicks an existing chip has plainly chosen the chip, and the
   * page should show them that the text no longer counts. So it empties the
   * box.
   *
   * Enhancement only. With scripting off the server behaves identically — the
   * typed name still wins — which is why this clears the input rather than
   * deciding anything (app/matters/services.py `resolve_addressee`).
   */
  function bindExclusiveName(scope) {
    (scope || document).querySelectorAll("[data-clears]").forEach(function (box) {
      if (!once(box, "ExclusiveName")) {
        return;
      }
      var group = box.getAttribute("data-clears");
      var field = document.getElementById(box.getAttribute("data-clears-field"));
      if (!field) {
        return;
      }
      box.querySelectorAll('input[type="radio"][name="' + group + '"]').forEach(function (radio) {
        radio.addEventListener("change", function () {
          if (radio.checked) {
            field.value = "";
          }
        });
      });
    });
  }

  /* Arriving from a number: put the reader on the rows.
   *
   * Every figure on Ulevaade links to `...#tulemused`, and a filtered register
   * opens with a search box, a status strip and a narrowing panel that expands
   * itself whenever a filter is active. The browser scrolls to the fragment on
   * its own; what it does not reliably do is *focus* it, so a keyboard or
   * screen-reader user landed at the top of the document and had to tab past
   * every control to reach the list they clicked a number to see.
   *
   * `preventScroll` because the browser has already scrolled, and focusing
   * again would fight it. Progressive: with JavaScript off the fragment still
   * scrolls, which is the part that matters most. */
  function focusFragmentTarget() {
    if (window.location.hash !== "#tulemused") return;
    var results = document.getElementById("tulemused");
    if (!results) return;
    try {
      results.focus({ preventScroll: true });
    } catch (error) {
      results.focus();
    }
  }

  /* ---- How many are chosen ------------------------------------------------
   * A count beside the label, for the rows where the chips wrap onto three
   * lines and "did I tick Ehitus?" costs a scan. Reads the controls the page
   * already has; adds nothing to what is posted.
   */
  function bindChipCounts(scope) {
    (scope || document).querySelectorAll("[data-chipcount-for]").forEach(function (badge) {
      if (!once(badge, "ChipCount")) {
        return;
      }
      var key = badge.getAttribute("data-chipcount-for");
      var form = badge.closest("form");
      if (!form) {
        return;
      }
      /* Either a field name — every chip in the group, wherever it is rendered
         — or one element's id, which is how the file input is counted. */
      var byName = form.querySelectorAll('input[name="' + key + '"]');
      var single = document.getElementById(key);
      var sources = byName.length ? Array.prototype.slice.call(byName) : single ? [single] : [];
      if (!sources.length) {
        return;
      }
      var sync = function () {
        var count = single && sources[0] === single
          ? (single.files || []).length
          : sources.filter(function (input) {
              return input.checked && input.value !== "";
            }).length;
        badge.textContent = count ? count + " valitud" : "";
      };
      sources.forEach(function (input) {
        input.addEventListener("change", sync);
      });
      sync();
    });
  }

  /* ---- The Hetkeseis tooltip ----------------------------------------------
   * Hover and focus are CSS. Two things are not, and both are corrections
   * rather than behaviour:
   *
   *  - a chip near the right edge would open its bubble off the screen, so the
   *    bubble is measured once it is visible and flipped to open leftwards;
   *  - Escape closes it, which a CSS `:hover` cannot hear. Suppression lasts
   *    until the pointer or the focus leaves the chip, so the next hover shows
   *    it again rather than the chip staying mute.
   *
   * With scripting off the tooltip still opens on hover and on focus and still
   * closes when either leaves; only the flip and Escape are missing
   * (Uus teema redesign §8).
   */
  function bindStageHelp(scope) {
    (scope || document).querySelectorAll(".chip--explained").forEach(function (chip) {
      if (!once(chip, "StageHelp")) {
        return;
      }
      var bubble = chip.querySelector(".stagehelp");
      if (!bubble) {
        return;
      }
      var place = function () {
        chip.classList.remove("is-suppressed");
        bubble.classList.remove("stagehelp--flip");
        var box = bubble.getBoundingClientRect();
        if (box.right > document.documentElement.clientWidth - 8) {
          bubble.classList.add("stagehelp--flip");
        }
      };
      var clear = function () {
        chip.classList.remove("is-suppressed");
      };
      chip.addEventListener("mouseenter", place);
      chip.addEventListener("mouseleave", clear);
      chip.addEventListener("focusin", place);
      chip.addEventListener("focusout", clear);
      chip.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
          chip.classList.add("is-suppressed");
        }
      });
    });
  }

  /* ---- A primary action that says whether it can do anything --------------
   * "Loo teema" reads inactive until there is a title, and it stays a working
   * button: pressing it anyway produces the server's refusal beside the field
   * rather than a control that does nothing and explains nothing.
   *
   * A data attribute, not `aria-disabled`. That attribute makes the claim this
   * one deliberately does not — a screen reader announces the button as
   * unavailable and a browser driver refuses to click it, which is exactly the
   * behaviour the flat fill is *not* meant to have.
   */
  function bindRequiredAction(scope) {
    (scope || document).querySelectorAll("button[data-needs]").forEach(function (button) {
      if (!once(button, "RequiredAction")) {
        return;
      }
      var field = document.getElementById(button.getAttribute("data-needs"));
      if (!field) {
        return;
      }
      var sync = function () {
        var ready = field.value.trim() !== "";
        button.setAttribute("data-inactive", ready ? "false" : "true");
      };
      field.addEventListener("input", sync);
      sync();
    });
  }

  /* ---- The persona popover -----------------------------------------------
   * The pill on the bar opens the same list the full page shows, so somebody
   * comparing two colleagues' queues stays on the queue instead of making a
   * round trip through /konto/kasutaja/ (Vali kasutaja brief 19).
   *
   * Progressive enhancement, as everything in this file is. With scripting off
   * the pill is a `<button type="button">` that does nothing and the popover is
   * `hidden` — so the full page stays the way to switch, which is why it stays
   * on the bar as a real route rather than being replaced by this.
   *
   * The options are real submit buttons in real forms, and they keep those
   * semantics: no `role="menuitem"`, which would replace what the element is
   * with a claim about a widget this is not. What is added here is the part
   * native buttons in a popup do not get for free — arrow keys between them,
   * Escape to close, a click outside to close, and focus put back on the pill
   * when it does (Vali kasutaja brief 26).
   */
  function bindPersonaMenu(scope) {
    (scope || document).querySelectorAll("[data-persona-trigger]").forEach(function (pill) {
      if (!once(pill, "PersonaMenu")) {
        return;
      }
      var menu = document.getElementById(pill.getAttribute("aria-controls"));
      if (!menu) {
        return;
      }

      var options = function () {
        return Array.prototype.slice.call(menu.querySelectorAll("[data-persona-option]"));
      };

      var isOpen = function () {
        return pill.getAttribute("aria-expanded") === "true";
      };

      /* `hidden` as well as the attribute, because the popover has to be out of
         the accessibility tree when it is shut — a `display: none` alone would
         do it, but then the state lives in a stylesheet and the attribute is a
         second copy of it that can drift. */
      var open = function (focusFirst) {
        pill.setAttribute("aria-expanded", "true");
        menu.hidden = false;
        if (focusFirst) {
          var first = options()[0];
          if (first) {
            first.focus();
          }
        }
      };

      var close = function (restoreFocus) {
        if (!isOpen()) {
          return;
        }
        pill.setAttribute("aria-expanded", "false");
        menu.hidden = true;
        if (restoreFocus) {
          pill.focus();
        }
      };

      var step = function (from, delta) {
        var all = options();
        if (!all.length) {
          return;
        }
        var index = all.indexOf(from);
        /* Wraps. A list of four names is short enough that running off the end
           and stopping feels like the key did not work. */
        var next = index < 0 ? (delta > 0 ? 0 : all.length - 1) : (index + delta + all.length) % all.length;
        all[next].focus();
      };

      pill.addEventListener("click", function () {
        if (isOpen()) {
          close(false);
        } else {
          open(false);
        }
      });

      /* Enter and Space already activate a button and reach the click handler
         above. The arrows are the addition: they open the popover *and* land on
         the first choice, which is what makes it operable without a pointer. */
      pill.addEventListener("keydown", function (event) {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          if (!isOpen()) {
            open(false);
          }
          var all = options();
          if (all.length) {
            all[event.key === "ArrowDown" ? 0 : all.length - 1].focus();
          }
        } else if (event.key === "Escape") {
          close(false);
        }
      });

      menu.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
          event.preventDefault();
          close(true);
          return;
        }
        var option = event.target.closest ? event.target.closest("[data-persona-option]") : null;
        if (!option) {
          return;
        }
        if (event.key === "ArrowDown") {
          event.preventDefault();
          step(option, 1);
        } else if (event.key === "ArrowUp") {
          event.preventDefault();
          step(option, -1);
        } else if (event.key === "Home") {
          event.preventDefault();
          step(null, 1);
        } else if (event.key === "End") {
          event.preventDefault();
          step(null, -1);
        }
      });

      /* Tabbing out of the popover closes it, without stealing the focus the
         person was moving towards. `focusout` fires before the new element is
         focused, so the check is deferred by a frame — `relatedTarget` is
         `null` in a few browsers here and asking the document afterwards is the
         answer that is always right. */
      menu.addEventListener("focusout", function () {
        window.setTimeout(function () {
          var active = document.activeElement;
          if (!menu.contains(active) && active !== pill) {
            close(false);
          }
        }, 0);
      });
    });
  }

  /* One listener for every popover on the page rather than one per pill, so a
     surface that arrives through HTMX cannot leave a second copy behind. */
  document.addEventListener("click", function (event) {
    document.querySelectorAll("[data-persona-trigger]").forEach(function (pill) {
      if (pill.getAttribute("aria-expanded") !== "true") {
        return;
      }
      var menu = document.getElementById(pill.getAttribute("aria-controls"));
      var inside = pill.contains(event.target) || (menu && menu.contains(event.target));
      if (!inside) {
        pill.setAttribute("aria-expanded", "false");
        if (menu) {
          menu.hidden = true;
        }
      }
    });
  });

  /* ---- Live suggestions under the header search --------------------------
   * The compact field already submitted to the full results page, and still
   * does. This is a shortcut past that page for the case it is nearly always
   * used for — "open that file" — and it is bound onto the existing form
   * rather than replacing it: without this script, or before it runs, or after
   * the endpoint fails, typing and pressing Enter goes exactly where it always
   * went (master specification 17.7).
   *
   * Nothing here decides what may be seen. The endpoint runs the same
   * authorized, ranked search the results page runs, five rows of it, and this
   * function renders whatever comes back. There is no filtering in the browser
   * to get wrong (app/search/views.py).
   *
   * No loading indicator, deliberately. The panel keeps the previous answer
   * while the next one is on its way, so there is never a blank to wait
   * through — and a spinner blinking on and off under a header field on every
   * third keystroke is the noise the design asked to avoid.
   */
  var SUGGEST_MIN_CHARACTERS = 2;
  var SUGGEST_DEBOUNCE_MS = 200;

  function bindLiveSearch(scope) {
    (scope || document).querySelectorAll("form[data-live-search]").forEach(function (form) {
      if (!once(form, "LiveSearch")) {
        return;
      }
      var input = form.querySelector(".searchfield__input");
      var panel = form.querySelector(".searchfield__results");
      var status = form.querySelector("[role=status]");
      var endpoint = form.getAttribute("data-live-search");
      /* No fetch means no suggestions and an untouched form, which is the
         correct outcome rather than a degraded one. */
      if (!input || !panel || !panel.id || !endpoint || typeof window.fetch !== "function") {
        return;
      }

      /* Announced only now that the behaviour exists. Writing these into the
         template would describe a listbox that nothing can open. */
      input.setAttribute("role", "combobox");
      input.setAttribute("aria-autocomplete", "list");
      input.setAttribute("aria-expanded", "false");
      input.setAttribute("aria-controls", panel.id);
      panel.setAttribute("role", "listbox");
      panel.setAttribute("aria-label", "Otsingusoovitused");

      var options = [];
      var active = -1;
      /* The stale-response guard. Every request takes the next number, and only
         a response still holding the current one may reach the page. The abort
         below usually stops a superseded request before it resolves; a response
         already parsed when the abort lands would otherwise arrive after a
         newer one and overwrite it — "maks" replacing "maksud". */
      var version = 0;
      var inFlight = null;
      var timer = null;

      function announce(text) {
        if (status) {
          status.textContent = text;
        }
      }

      function close() {
        panel.hidden = true;
        panel.textContent = "";
        options = [];
        active = -1;
        input.setAttribute("aria-expanded", "false");
        input.removeAttribute("aria-activedescendant");
      }

      function setActive(index) {
        if (active >= 0 && options[active]) {
          options[active].setAttribute("aria-selected", "false");
          options[active].classList.remove("is-active");
        }
        active = index;
        if (active >= 0 && options[active]) {
          var option = options[active];
          option.setAttribute("aria-selected", "true");
          option.classList.add("is-active");
          /* What the field is pointing at, without moving focus out of it —
             which is what lets somebody keep typing while a row is selected. */
          input.setAttribute("aria-activedescendant", option.id);
          if (option.scrollIntoView) {
            option.scrollIntoView({ block: "nearest" });
          }
        } else {
          input.removeAttribute("aria-activedescendant");
        }
      }

      function makeOption(id, href, modifier) {
        var option = document.createElement("a");
        option.className = "searchfield__option" + (modifier ? " " + modifier : "");
        option.id = id;
        option.setAttribute("role", "option");
        option.setAttribute("aria-selected", "false");
        /* Reachable by the arrows and by the pointer, never by Tab: a listbox
           is one stop, and five extra tab stops under the header would be a
           worse keyboard than the one this replaces. */
        option.setAttribute("tabindex", "-1");
        option.href = href;
        return option;
      }

      function render(payload) {
        panel.textContent = "";
        options = [];
        active = -1;
        input.removeAttribute("aria-activedescendant");

        var results = payload.results || [];
        results.forEach(function (result, index) {
          var option = makeOption(panel.id + "-" + index, result.url, "");
          var title = document.createElement("span");
          title.className = "searchfield__optiontitle";
          /* textContent throughout. Nothing the server sends is ever parsed as
             markup here, so a Matter titled with a tag stays a title. */
          title.textContent = result.title;
          option.appendChild(title);
          if (result.context) {
            var context = document.createElement("span");
            context.className = "searchfield__optionmeta";
            context.textContent = result.context;
            option.appendChild(context);
          }
          panel.appendChild(option);
          options.push(option);
        });

        if (!results.length) {
          var empty = document.createElement("p");
          empty.className = "searchfield__empty";
          /* An option rather than loose text, because the only valid child of a
             listbox is an option — and a disabled one, because there is nothing
             here to choose. The arrows skip it: it is never pushed onto
             `options`. */
          empty.setAttribute("role", "option");
          empty.setAttribute("aria-disabled", "true");
          empty.setAttribute("aria-selected", "false");
          empty.textContent = "Tulemusi ei leitud";
          panel.appendChild(empty);
        } else if (payload.has_more && payload.all_url) {
          /* The corpus is wider than this list: the full page also answers with
             entries, sent opinions and pages of annexes. One row leading to it,
             rather than a second search built into the header. */
          var all = makeOption(panel.id + "-koik", payload.all_url, "searchfield__option--all");
          all.textContent = "Vaata kõiki tulemusi";
          panel.appendChild(all);
          options.push(all);
        }

        panel.hidden = false;
        input.setAttribute("aria-expanded", "true");
        if (!results.length) {
          announce("Tulemusi ei leitud");
        } else if (results.length === 1) {
          announce("1 soovitus");
        } else {
          announce(results.length + " soovitust");
        }
      }

      function abortInFlight() {
        if (inFlight) {
          inFlight.abort();
          inFlight = null;
        }
      }

      function request(term) {
        var token = ++version;
        abortInFlight();
        var controller = typeof AbortController === "function" ? new AbortController() : null;
        inFlight = controller;
        window
          .fetch(endpoint + "?q=" + encodeURIComponent(term), {
            credentials: "same-origin",
            headers: { Accept: "application/json" },
            signal: controller ? controller.signal : undefined,
          })
          .then(function (response) {
            /* A redirect to the sign-in page arrives here as an HTML 200, and a
               refusal as a 4xx. Neither is a result set, and both mean the same
               thing to this control: leave the form alone. */
            var type = response.headers.get("content-type") || "";
            if (!response.ok || type.indexOf("application/json") < 0) {
              throw new Error("otsingusoovitusi ei saadud");
            }
            return response.json();
          })
          .then(function (payload) {
            if (token !== version) {
              return;
            }
            render(payload);
          })
          .catch(function () {
            if (token !== version) {
              return;
            }
            /* The suggestions go away; the form does not. Enter still reaches
               the full results page. */
            close();
            announce("");
          });
      }

      function schedule() {
        window.clearTimeout(timer);
        var term = input.value.replace(/\s+/g, " ").trim();
        if (term.replace(/\s+/g, "").length < SUGGEST_MIN_CHARACTERS) {
          /* Below the threshold nothing is asked at all, and anything already
             asked stops counting — otherwise deleting back to one character
             would leave the last answer sitting under an all-but-empty field. */
          version += 1;
          abortInFlight();
          close();
          announce("");
          return;
        }
        timer = window.setTimeout(function () {
          request(term);
        }, SUGGEST_DEBOUNCE_MS);
      }

      input.addEventListener("input", schedule);

      input.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
          if (!panel.hidden) {
            /* Only when there is something to close, so Escape keeps its
               ordinary meaning for the field the rest of the time. */
            event.preventDefault();
            event.stopPropagation();
            close();
            announce("");
            input.focus();
          }
          return;
        }
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          if (panel.hidden || !options.length) {
            return;
          }
          event.preventDefault();
          var delta = event.key === "ArrowDown" ? 1 : -1;
          setActive(
            active < 0
              ? delta > 0
                ? 0
                : options.length - 1
              : (active + delta + options.length) % options.length
          );
          return;
        }
        if (event.key === "Enter") {
          /* Only when a row is selected. With nothing selected this is the
             ordinary submit, and the ordinary submit is the fallback. */
          if (!panel.hidden && active >= 0 && options[active]) {
            event.preventDefault();
            var target = options[active].href;
            close();
            window.location.assign(target);
          }
          return;
        }
        if (event.key === "Tab") {
          close();
        }
      });

      /* Keeps the focus in the field while a row is being clicked. Without this
         the blur below fires first, the panel is gone before the click lands,
         and the row cannot be clicked at all. */
      panel.addEventListener("mousedown", function (event) {
        event.preventDefault();
      });

      input.addEventListener("blur", function () {
        window.setTimeout(function () {
          if (!form.contains(document.activeElement)) {
            close();
          }
        }, 0);
      });

      document.addEventListener("click", function (event) {
        if (!form.contains(event.target)) {
          close();
        }
      });

      form.addEventListener("submit", close);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    bind(document);
    bindLiveSearch(document);
    bindPeriodFields(document);
    bindDatePickers(document);
    bindChoiceFilters(document);
    bindExclusiveName(document);
    bindChipCounts(document);
    bindStageHelp(document);
    bindRequiredAction(document);
    bindPersonaMenu(document);
    focusFragmentTarget();
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
    bindChoiceFilters(event.target.querySelector ? event.target : document);
    bindExclusiveName(event.target.querySelector ? event.target : document);
    bindChipCounts(event.target.querySelector ? event.target : document);
    bindStageHelp(event.target.querySelector ? event.target : document);
    bindRequiredAction(event.target.querySelector ? event.target : document);
    bindPersonaMenu(event.target.querySelector ? event.target : document);
  });
})();
