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

  /* ---- Arriving at the next step from another page ----------------------
   * `Määra` on Minu asjad, and `Muuda` / `Märgi tehtuks` / `Vaatasin üle…` in a
   * work row's menu, are the product's most repeated request — and all four are
   * links to *another* page, so nothing bound on this page runs for them. They
   * used to name `#jargmiseks`, which is not an id anything renders; the
   * browser found nothing, and arrival left the reader at the top of the
   * document with the composer shut (UX-003).
   *
   * They now name the two elements that actually exist, and this opens the
   * destination, scrolls it into view and puts the cursor in it on arrival.
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
  /* The ids a link from another page may name, and every one of them is an
     element some render of the Teema page really has.

     `lisa-jargmine` is `Muuda` beside a task and `+ Lisa tegevus` on a Matter
     with none (docs/adr/0126 §1) — Minu asjad's `Määra` lands there. It
     landed on `lisa-marge` (`+ Märge · Tavaline`) until that left `+ Lisa`
     on 2026-10-07; an old link naming it now does nothing rather than open a
     deadline form.

     `vaatasin-ule` is Minu asjad's `Vaatasin üle…`: the review disclosure
     beside a step that waits, opened with the caret in its date box. Where the
     step has since become a plan the panel is not drawn, and arrival falls back
     to `PRAEGUNE TEGEVUS` like the others (ENG-021). */
  var NEXT_STEP_TARGETS = ["praegune-tegevus", "lisa-jargmine", "vaatasin-ule"];

  /* Open whatever kind of disclosure this destination is, and say whether it
   * was one.
   *
   * Two shapes, because the destinations are two kinds of control:
   * `#lisa-jargmine` is `Muuda` in PRAEGUNE TEGEVUS, a lone `<details>`;
   * `#lisa-marge` is a `LISA TEEMALE` panel, which since 2026-09-14 is a plain
   * element revealed by its own radio. Both have to be opened before the
   * scroll, or the browser centres a box of the wrong height and the field
   * inside it is not focusable at all. */
  function revealDisclosure(target) {
    if (!target) {
      return false;
    }
    if (target.tagName === "DETAILS") {
      target.open = true;
      return true;
    }
    var pick = target.id ? document.getElementById(target.id + "-valik") : null;
    if (!pick) {
      return false;
    }
    pick.checked = true;
    return true;
  }

  /* `✓ Tehtud`, opened, and the `Mida tegid?` box inside it — or `null` on a
     Matter with no open step or for a reader who may not write. By id: the
     current step's own typed form, drawn before it, has a textarea too
     (docs/adr/0133 §4, §6). The panel opens off its checkbox, as a
     `LISA TEEMALE` panel opens off its radio (docs/adr/0140 §1). */
  function openDoneForm() {
    var pick = document.getElementById("tehtud-valik");
    var box = document.getElementById("id_praegune_body");
    if (!pick || !box) {
      return null;
    }
    pick.checked = true;
    return box;
  }

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
    if (revealDisclosure(target)) {
      /* Opened before scrolling, so the box is its real height when it is
         centred, and so the field inside it is focusable at all. */
      target.scrollIntoView({ block: "center", behavior: "auto" });
      /* `[data-composer-focus]` first, then the first visible control. Not
         `input` in general: every form here opens with a hidden CSRF token.

         The attribute names the box the person is meant to type in where
         the first control is something else — a date box that arrives
         already filled, say. */
      var box =
        target.querySelector("[data-composer-focus]") ||
        target.querySelector("textarea, select, input:not([type=hidden])");
      if (box) {
        box.focus();
      }
      return;
    }
    /* `PRAEGUNE TEGEVUS`. The control this link promised is the box that
       records what was done, so that is what takes the cursor — behind
       `✓ Tehtud` since docs/adr/0133, which is opened first; a Matter with no
       open task, or a reader who may not write, renders no box and the zone
       itself is focused through its `tabindex="-1"`. */
    var done = openDoneForm();
    target.scrollIntoView({ block: "center", behavior: "auto" });
    focusQuietly(done || target);
  }

  document.addEventListener("keydown", function (event) {
    if (event.ctrlKey || event.metaKey || event.altKey || isEditing(event.target)) {
      return;
    }
    if (event.key !== "l" && event.key !== "L") {
      return;
    }
    /* `L` for «lisa»: the box where ordinary work gets written down. On a
       Matter with a current task that is `✓ Tehtud`'s `Mida tegid?`; on one
       without, it is `+ Lisa tegevus` in `PRAEGUNE TEGEVUS` — the one
       ordinary way to add work since `+ Lisa · Tavaline` left (2026-10-07).
       Never a `+ Lisa` sub-choice: `Arvamuse tähtaeg` is first there, and a
       shortcut for work is not a shortcut for a deadline. */
    var box = openDoneForm();
    if (box) {
      event.preventDefault();
      box.focus();
      return;
    }
    var addWork = document.getElementById("lisa-jargmine");
    if (!addWork || addWork.tagName !== "DETAILS") {
      return;
    }
    event.preventDefault();
    addWork.open = true;
    addWork.scrollIntoView({ block: "center", behavior: "auto" });
    var first = addWork.querySelector("[data-composer-focus]") ||
      addWork.querySelector("textarea, input:not([type=hidden]), select");
    if (first) {
      focusQuietly(first);
    }
  });

  /* `+ Lisa → Arvamuse tähtaeg` beside a request that is still current: the
     link opens the header's own `Arvamuse tähtaeg` editor, where moving or
     replacing it is asked. Without scripting it is a link to the header. */
  document.addEventListener("click", function (event) {
    var link = event.target.closest ? event.target.closest("[data-open-deadline-editor]") : null;
    if (!link) {
      return;
    }
    var editor = document.querySelector("[data-deadline-editor]");
    if (!editor) {
      return;
    }
    event.preventDefault();
    editor.open = true;
    editor.scrollIntoView({ block: "center", behavior: "auto" });
    var field = editor.querySelector("input:not([type=hidden])");
    if (field) {
      focusQuietly(field);
    }
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

  /* ---- Where the publication-date default used to live -------------------
   * `bindPublicationDate` filled the `+ Ülevaade / uudis` date box with today
   * the moment somebody started typing an address, on the reasoning that
   * pasting a link *is* choosing the published path and retyping today's date
   * after that is pure friction (ADR 0085 §3).
   *
   * Lawyer testing measured the other half of that trade. The date appeared
   * before anybody had thought about it, it looked correct, and it was
   * accepted — so the file filled up with publication dates the application had
   * proposed and nobody had checked, indistinguishable afterwards from dates
   * somebody knew. The commonest real save turned out to be an address out of a
   * search result whose publication day is genuinely unknown.
   *
   * So the island is withdrawn, along with the server-side `initial` on the
   * publish form's own date box, and an empty date box now means *unknown* —
   * which is a thing the record may hold since ADR 0089 §8. There is no
   * replacement behaviour here on purpose: the honest default for a fact nobody
   * knows is nothing at all.
   */

  /* ---- A refused save takes the person to the thing that needs fixing ----
   * A save can be refused correctly and still leave nobody any the wiser. Two
   * ways it happened:
   *
   *   * On the Teema page every save swaps `#teema-vaade`, and a swap leaves
   *     focus on `<body>` — so somebody who pressed `Salvesta` landed at the
   *     top of the document with the explanation somewhere below them.
   *   * On `Uus teema` the refusal is a full-page render, and the first
   *     genuinely invalid field is often `Järgmiseks`, eleven blocks down. The
   *     page came back looking unchanged, with the reason off-screen.
   *
   * The message is announced by its `role="alert"`; this is the other half, and
   * it is what «errors move focus appropriately» means (§33).
   *
   * **The control that is wrong, not the first control on the form.** This used
   * to focus whatever the form's first field happened to be, which on a long
   * form is a different field from the one that was refused — so it scrolled
   * somebody to the top of a form to look at a box that was perfectly fine.
   */
  var FOCUSABLE = "textarea:not([hidden]), select:not([hidden]), " +
    "input:not([type=hidden]):not([hidden])";

  /* The container conventions this application renders an error inside. The
     error is written *after* the control it belongs to everywhere, so the
     container is the reliable way back to it. */
  var FIELD_CONTAINERS = "label, .field, fieldset, .cx-f, .createform__row, .addform";

  function controlForError(problem, form) {
    /* Inside the same field wrapper, which is the common case and the exact
       answer: one label, one control, one message under it. */
    var container = problem.closest(FIELD_CONTAINERS);
    if (container) {
      var owned = container.querySelector(FOCUSABLE);
      if (owned) {
        return owned;
      }
    }
    /* Otherwise the nearest control *above* the message. `Millal?` puts its
       refusal on the row rather than inside the «Kuupäev…» disclosure, so the
       message and the box are siblings rather than parent and child. */
    var previous = null;
    Array.prototype.some.call(form.querySelectorAll(FOCUSABLE), function (control) {
      if (problem.compareDocumentPosition(control) & Node.DOCUMENT_POSITION_PRECEDING) {
        previous = control;
        return false;
      }
      return true;
    });
    return previous;
  }

  function revealAndFocus(target, anchor) {
    /* Every closed disclosure between here and the surface, opened outermost
       first: a field inside a closed `<details>` has no box to scroll to and
       cannot take focus at all. */
    var node = target;
    var closed = [];
    while (node) {
      var details = node.closest ? node.closest("details:not([open])") : null;
      if (!details) {
        break;
      }
      closed.unshift(details);
      node = details.parentElement;
    }
    closed.forEach(function (details) {
      details.open = true;
    });

    /* Immediate, not smooth: this is a correction, not a tour. The clearance
       under the sticky bar is `scroll-margin-top` in the stylesheet rather than
       an offset computed here, so the bar's height lives in one place. */
    (anchor || target).scrollIntoView({ block: "start", behavior: "auto" });
    if (target && target.focus) {
      focusQuietly(target);
    }
  }

  function focusFirstRefusal(scope) {
    var root = scope && scope.querySelector ? scope : document;
    /* Nothing at all when the save succeeded — this must never take the cursor
       off an ordinary page load. */
    var problem = root.querySelector(".field__error");
    if (!problem) {
      /* A form-level refusal with no field of its own: «Kirjelda tegevust või
         vali, mida veel salvestada» names no box, so guessing one would put the
         cursor somewhere the message is not about. The summary itself takes it.

         `tabindex` is set here rather than written into twenty-one templates.
         A paragraph is not focusable, and `-1` makes it focusable to script
         without putting it in the tab order — so the announcement is reachable
         and nothing new appears between two fields for a keyboard user. Set at
         the moment of use, so the attribute only ever exists on a message that
         is actually on the page. */
      var summary = root.querySelector(".formerror");
      if (summary) {
        summary.setAttribute("tabindex", "-1");
        revealAndFocus(summary, summary);
      }
      return;
    }
    var form = problem.closest("form");
    if (!form) {
      return;
    }
    var field = controlForError(problem, form);
    if (field) {
      revealAndFocus(field, field.closest(FIELD_CONTAINERS) || field);
    } else {
      problem.setAttribute("tabindex", "-1");
      revealAndFocus(problem, problem);
    }
  }

  /* ---- LISA TEEMALE: the chip you already chose closes the form ---------
   * The zone is a *choice* of seven operations, and the browser now keeps that
   * on its own: one radio `name` means choosing a second operation unchecks the
   * first, `:checked` reveals its form and paints its chip, and a refusal that
   * comes back with the radio checked server-side reopens the panel it came
   * from. None of that needs a script, which is why none of it is here
   * (templates/matters/partials/add_to_matter.html).
   *
   * What a radio group cannot do is go back to nothing chosen. This adds that
   * one behaviour: clicking the chip that is already active puts the bar back
   * to seven closed choices. With scripting off the bar still opens every form
   * and switches between them — what is lost is the click-to-close, not the
   * capability.
   *
   * Deliberately **not** `data-uxpopover`. That contract also closes on any
   * click outside the disclosure, which is right for a menu and wrong for a
   * form somebody is typing into: the browser lane caught it shutting under the
   * cursor mid-entry and taking the field with it (docs/adr/0074 §20).
   *
   * `Muuda` in PRAEGUNE TEGEVUS is still a lone `<details>` and needs nothing
   * here: it has no siblings to close and nothing to un-choose.
   */
  function bindAddPanels(scope) {
    scope.querySelectorAll("[data-addpick]").forEach(function (pick) {
      if (!once(pick, "AddPick")) {
        return;
      }
      /* The label is the visible control, so the click arrives there and is
         forwarded to the radio by the browser. Intercepting it on the label —
         before the forwarding — is what makes "already chosen" observable at
         all: by the time a `click` reaches the input it is checked either way. */
      var chip = pick.parentNode
        ? pick.parentNode.querySelector('label[for="' + pick.id + '"]')
        : null;
      if (!chip) {
        return;
      }
      chip.addEventListener("click", function (event) {
        if (!pick.checked) {
          return;
        }
        event.preventDefault();
        pick.checked = false;
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
      show();
    });
  }

  /* ---- A display title, edited only when asked --------------------------
   * A queued file reads as one line — its title, a ✎, its size, a × — and the
   * title is the filename until somebody changes it. Only the ✎ turns it into
   * a box, and only for that file: a queue of eight files is eight lines to
   * read, not eight boxes to tab through (owner's round, 2026-10-08).
   *
   * One behaviour, delegated on the document, for every row that carries this
   * markup — the queue rows built below and Uus teema's staged rows, which the
   * server renders (templates/matters/partials/intake_files.html):
   *
   *   <span class="titleedit" data-title-edit data-title-original="a.pdf">
   *     <span class="titleedit__text" data-title-edit-text>a.pdf</span>
   *     <button type="button" class="titleedit__open" data-title-edit-open
   *             aria-label="Muuda pealkirja: a.pdf" title="Muuda pealkirja">✎</button>
   *     <span class="titleedit__original" data-title-edit-original hidden>a.pdf</span>
   *     <input type="hidden" name="…" value="a.pdf" data-title-edit-value>
   *   </span>
   *
   * The hidden input is what the form posts, and it changes only on a
   * confirm — Enter, or leaving the box. Escape puts the previous title back,
   * and so does confirming an empty box: a file always has a title. The box
   * itself is unnamed, so an edit still open never posts a value beside the
   * hidden one (`UploadTitlesMiddleware` pairs titles and files by count).
   * Once the title differs from the filename, the filename stays on the row,
   * muted, because it is what the stored version keeps.
   */
  var TITLE_MAX_LENGTH = 400;

  /* Whether a pointer is pressed right now. Leaving the box by pressing a
     button — `Salvesta`, another file's `×` — blurs it on the press; closing
     it then reshapes the row under the pointer before the release, and the
     click can land beside what it was aimed at. So the value is kept at once
     and the box closes after the release. */
  var pointerHeld = false;
  document.addEventListener(
    "pointerdown",
    function () {
      pointerHeld = true;
    },
    true
  );
  ["pointerup", "pointercancel"].forEach(function (name) {
    document.addEventListener(
      name,
      function () {
        pointerHeld = false;
      },
      true
    );
  });

  function afterRelease(callback) {
    var done = false;
    var run = function () {
      if (done) {
        return;
      }
      done = true;
      document.removeEventListener("pointerup", run, true);
      document.removeEventListener("pointercancel", run, true);
      /* After the click the release produces, which is dispatched in the
         same task. */
      window.setTimeout(callback, 0);
    };
    document.addEventListener("pointerup", run, true);
    document.addEventListener("pointercancel", run, true);
  }

  function titleParts(box) {
    return {
      text: box.querySelector("[data-title-edit-text]"),
      open: box.querySelector("[data-title-edit-open]"),
      original: box.querySelector("[data-title-edit-original]"),
      value: box.querySelector("[data-title-edit-value]"),
      editor: box.querySelector("[data-title-edit-input]"),
    };
  }

  /* Paints the confirmed title, and the filename beside it once they differ. */
  function showTitle(box) {
    var parts = titleParts(box);
    if (!parts.value) {
      return;
    }
    var title = parts.value.value;
    var original = box.getAttribute("data-title-original") || "";
    if (parts.text) {
      parts.text.textContent = title;
    }
    if (parts.original) {
      parts.original.hidden = !original || title === original;
    }
  }

  /* The title a redraw should keep: what is confirmed, or what is being
     typed if the box is still open — a second drop while somebody is
     mid-word is not a reason to lose the word. */
  function currentTitle(box) {
    var parts = titleParts(box);
    var typed = parts.editor ? parts.editor.value.trim() : "";
    return typed || (parts.value ? parts.value.value : "");
  }

  function openTitleEdit(box) {
    var parts = titleParts(box);
    if (!parts.value) {
      return null;
    }
    if (parts.editor) {
      parts.editor.focus();
      return parts.editor;
    }
    var previous = parts.value.value;
    var original = box.getAttribute("data-title-original") || previous;
    var editor = document.createElement("input");
    editor.type = "text";
    editor.className = "field__input field__input--compact titleedit__input";
    editor.maxLength = TITLE_MAX_LENGTH;
    editor.autocomplete = "off";
    editor.value = previous;
    editor.setAttribute("aria-label", "Pealkiri: " + original);
    editor.setAttribute("data-title-edit-input", "");

    var closed = false;
    var keep = function () {
      parts.value.value = editor.value.trim() || previous;
    };
    var close = function (refocus) {
      if (closed) {
        return;
      }
      closed = true;
      editor.remove();
      [parts.text, parts.open].forEach(function (part) {
        if (part) {
          part.hidden = false;
        }
      });
      showTitle(box);
      if (refocus && parts.open) {
        parts.open.focus();
      }
    };

    editor.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        keep();
        if (event.ctrlKey || event.metaKey) {
          /* The surrounding form's own save shortcut (static/js/app.js): it
             goes on, and posts the title just typed. */
          return;
        }
        /* Never the form's implicit submission. */
        event.preventDefault();
        event.stopPropagation();
        close(true);
      } else if (event.key === "Escape") {
        /* Stopped here, so the panel around it stays open. */
        event.preventDefault();
        event.stopPropagation();
        close(true);
      }
    });
    editor.addEventListener("blur", function () {
      if (closed || !editor.isConnected) {
        return;
      }
      keep();
      if (pointerHeld) {
        afterRelease(function () {
          close(false);
        });
      } else {
        close(false);
      }
    });

    [parts.text, parts.open, parts.original].forEach(function (part) {
      if (part) {
        part.hidden = true;
      }
    });
    box.insertBefore(editor, box.firstChild);
    editor.focus();
    editor.select();
    return editor;
  }

  document.addEventListener("click", function (event) {
    var opener = event.target.closest ? event.target.closest("[data-title-edit-open]") : null;
    var box = opener && opener.closest("[data-title-edit]");
    if (!box) {
      return;
    }
    event.preventDefault();
    openTitleEdit(box);
  });

  /* A title put back from outside — a poll re-rendering Uus teema's staged
     rows restores what was confirmed (static/js/app.js `applyFragment`) — is
     announced with a `change` on the hidden input, and the text follows. */
  document.addEventListener("change", function (event) {
    var value = event.target;
    var box =
      value && value.matches && value.matches("[data-title-edit-value]")
        ? value.closest("[data-title-edit]")
        : null;
    if (box) {
      showTitle(box);
    }
  });

  /* The same markup as the server renders, for a row built here. */
  function titleEditFor(name, title, original) {
    var box = document.createElement("span");
    box.className = "titleedit";
    box.setAttribute("data-title-edit", "");
    box.setAttribute("data-title-original", original);

    var text = document.createElement("span");
    text.className = "titleedit__text";
    text.setAttribute("data-title-edit-text", "");
    box.appendChild(text);

    var open = document.createElement("button");
    open.type = "button";
    open.className = "titleedit__open";
    open.textContent = "✎";
    open.title = "Muuda pealkirja";
    open.setAttribute("aria-label", "Muuda pealkirja: " + original);
    open.setAttribute("data-title-edit-open", "");
    box.appendChild(open);

    var shown = document.createElement("span");
    shown.className = "titleedit__original";
    shown.textContent = original;
    shown.setAttribute("data-title-edit-original", "");
    box.appendChild(shown);

    var value = document.createElement("input");
    value.type = "hidden";
    value.name = name;
    value.value = title;
    value.setAttribute("data-title-edit-value", "");
    box.appendChild(value);

    showTitle(box);
    return box;
  }

  /* ---- The upload queue: one for every file control ---------------------
   * Every dropzone — the workspace panels' dashed box (`[data-filedrop]`) and
   * Uus teema's (`[data-upload-zone]`) — shares this, once, by delegation on
   * the document, so a panel swapped in by HTMX needs no binding of its own
   * (owner's round, 2026-10-07):
   *
   * - a dropped file joins the SAME queue as a picked one, added to what is
   *   already chosen rather than replacing it;
   * - each queued file is listed with its display title — the filename by
   *   default, changed with the ✎ beside it (above) — posted as
   *   `<field>__pealkiri` in the files' own order (app/core/middleware.py
   *   `UploadTitlesMiddleware`), its size and a `×`;
   * - a file dragged anywhere else on the page is refused rather than opened
   *   by the browser, so a near miss never navigates away from the form.
   */
  var canTransfer = (function () {
    try {
      return typeof DataTransfer === "function" && !!new DataTransfer().items;
    } catch (error) {
      return false;
    }
  })();

  function uploadSize(bytes) {
    if (bytes < 1024) {
      return bytes + " B";
    }
    if (bytes < 1024 * 1024) {
      return Math.round(bytes / 1024) + " KB";
    }
    return (bytes / (1024 * 1024)).toFixed(1).replace(".", ",") + " MB";
  }

  function uploadZoneOf(node) {
    return node && node.closest ? node.closest("[data-filedrop], [data-upload-zone]") : null;
  }

  function uploadQueueOf(field) {
    var zone = uploadZoneOf(field);
    if (!zone) {
      return null;
    }
    var inside = zone.querySelector("[data-upload-queue]");
    if (inside) {
      return inside;
    }
    var next = zone.nextElementSibling;
    if (next && next.hasAttribute("data-upload-queue")) {
      return next;
    }
    var list = document.createElement("ul");
    list.className = "uploadqueue";
    list.setAttribute("data-upload-queue", "");
    list.hidden = true;
    zone.parentNode.insertBefore(list, zone.nextSibling);
    return list;
  }

  /* The titles given so far, in the queue's order, so a redraw — a removal, a
     second drop — keeps what somebody wrote beside each file. */
  function typedUploadTitles(field) {
    var list = uploadQueueOf(field);
    return list
      ? Array.prototype.map.call(list.querySelectorAll("[data-title-edit]"), currentTitle)
      : [];
  }

  function setUploadFiles(field, files, titles) {
    var transfer = new DataTransfer();
    files.forEach(function (file) {
      transfer.items.add(file);
    });
    field.files = transfer.files;
    field.uploadQueueTitles = titles || null;
    field.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function addUploadFiles(field, dropped) {
    var incoming = Array.prototype.slice.call(dropped || []);
    if (!incoming.length) {
      return;
    }
    if (!field.multiple) {
      incoming = incoming.slice(0, 1);
    }
    if (!canTransfer) {
      /* No DataTransfer to build with: the browser's own assignment is all
         there is, and a second drop replaces the first. */
      field.files = dropped;
      field.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }
    var current = field.multiple ? Array.prototype.slice.call(field.files || []) : [];
    var titles = field.multiple ? typedUploadTitles(field) : [];
    setUploadFiles(
      field,
      current.concat(incoming),
      titles.concat(
        incoming.map(function (file) {
          return file.name;
        })
      )
    );
  }

  function renderUploadQueue(field) {
    var list = uploadQueueOf(field);
    if (!list) {
      return;
    }
    list.textContent = "";
    var files = Array.prototype.slice.call(field.files || []);
    if (list.hasAttribute("data-upload-queue-hidden") || !files.length) {
      list.hidden = true;
      return;
    }
    list.hidden = false;
    var kept = field.uploadQueueTitles || [];
    field.uploadQueueTitles = null;
    files.forEach(function (file, index) {
      var row = document.createElement("li");
      /* Inside Uus teema's dropzone list the row is also that list's own row
         kind, so the list reads as one list whatever filled it. */
      row.className = list.classList.contains("dropzone__list")
        ? "uploadqueue__row dropzone__file"
        : "uploadqueue__row";

      var title = typeof kept[index] === "string" && kept[index] ? kept[index] : file.name;
      row.appendChild(titleEditFor(field.name + "__pealkiri", title, file.name));

      var size = document.createElement("span");
      size.className = "uploadqueue__size";
      size.textContent = uploadSize(file.size);
      row.appendChild(size);

      if (canTransfer) {
        var remove = document.createElement("button");
        remove.type = "button";
        remove.className = "quietbutton uploadqueue__remove";
        remove.textContent = "×";
        remove.title = "Eemalda fail";
        remove.setAttribute("aria-label", "Eemalda fail " + file.name);
        remove.addEventListener("click", function () {
          var others = function (unused, other) {
            return other !== index;
          };
          setUploadFiles(field, files.filter(others), typedUploadTitles(field).filter(others));
        });
        row.appendChild(remove);
      }
      list.appendChild(row);
    });
  }

  /* ---- `Tehtud` on a waiting round -------------------------------------
   * Opens the one `Lisa tagasiside` form — `+ Kaasamine`, then its second
   * choice — with this round chosen, and puts the caret in it. The same form
   * `LISA TEEMALE` offers, not a second one (owner's round, 2026-10-07).
   */
  document.addEventListener("click", function (event) {
    var opener = event.target.closest ? event.target.closest("[data-open-feedback]") : null;
    if (!opener) {
      return;
    }
    var launcher = document.getElementById("lisa-kaasamine-valik");
    var mode = document.getElementById("kaasamine-tagasiside-valik");
    if (!launcher || !mode) {
      return;
    }
    event.preventDefault();
    [launcher, mode].forEach(function (radio) {
      if (!radio.checked) {
        radio.checked = true;
        radio.dispatchEvent(new Event("change", { bubbles: true }));
      }
    });
    var panel = document.getElementById("kaasamine-tagasiside");
    var round = panel && panel.querySelector("select[name=engagement]");
    if (round) {
      round.value = opener.getAttribute("data-open-feedback");
      round.dispatchEvent(new Event("change", { bubbles: true }));
    }
    if (panel) {
      panel.scrollIntoView({ block: "center" });
      var first = panel.querySelector("textarea, input[type=text]");
      if (first) {
        first.focus({ preventScroll: true });
      }
    }
  });

  function draggingFiles(event) {
    var types = event.dataTransfer && event.dataTransfer.types;
    return !!types && Array.prototype.indexOf.call(types, "Files") !== -1;
  }

  function markDragover(zone) {
    document.querySelectorAll(".is-dragover, .is-over").forEach(function (other) {
      if (other !== zone) {
        other.classList.remove("is-dragover", "is-over");
      }
    });
    if (zone) {
      zone.classList.add(zone.hasAttribute("data-upload-zone") ? "is-over" : "is-dragover");
    }
  }

  ["dragenter", "dragover"].forEach(function (name) {
    document.addEventListener(name, function (event) {
      if (!draggingFiles(event)) {
        return;
      }
      /* Always: a file let go of anywhere on this application is never handed
         to the browser to open. Only a zone accepts it. */
      event.preventDefault();
      var zone = uploadZoneOf(event.target);
      event.dataTransfer.dropEffect = zone ? "copy" : "none";
      markDragover(zone);
    });
  });
  document.addEventListener("dragleave", function (event) {
    var zone = uploadZoneOf(event.target);
    if (zone && !zone.contains(event.relatedTarget)) {
      zone.classList.remove("is-dragover", "is-over");
    }
  });
  document.addEventListener("drop", function (event) {
    if (!draggingFiles(event)) {
      return;
    }
    event.preventDefault();
    markDragover(null);
    var zone = uploadZoneOf(event.target);
    var field = zone && zone.querySelector("input[type=file]");
    if (field && !field.disabled) {
      addUploadFiles(field, event.dataTransfer.files);
    }
  });
  document.addEventListener("change", function (event) {
    var field = event.target;
    if (field && field.type === "file" && uploadZoneOf(field)) {
      renderUploadQueue(field);
    }
  });

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
    if (menu.getAttribute("data-uxfloat") === "fit") {
      fit(menu, anchor, margin);
      return;
    }
    menu.style.top = anchor.bottom + 4 + "px";
    var left = Math.min(anchor.left, window.innerWidth - menu.offsetWidth - margin);
    menu.style.left = Math.max(margin, left) + "px";
  }

  /* `data-uxfloat="fit"` — a panel that must stay whole inside the window.

     Dokumendid's row `⋯` (templates/matters/matter_documents.html). Its trigger
     is the last control on the row, at the table's right edge, and the panel
     can be long — an opinion's carries the whole send record — so the plain
     placement above, left edge under the trigger and always downwards, would
     run it off the right of a phone and off the bottom of any window for a row
     low on the screen. A fixed box past the window's edge cannot be scrolled
     to, so this keeps it inside:

     - **end-aligned**: the panel's right edge under the trigger's, then
       clamped to the window with the same 8 px margin;
     - **downwards unless there is more room above**, so the last rows open
       upwards rather than into the bottom edge;
     - **never taller than that room**: the stylesheet caps it and it scrolls
       inside itself, and this lowers the cap further when the room is smaller.

     The panel's own scroll position is kept, because measuring it uncapped
     would otherwise send a reader who had scrolled inside it back to the top
     every time the page moved. */
  function fit(menu, anchor, margin) {
    var gap = 4;
    var kept = menu.scrollTop;
    menu.style.maxHeight = "";
    var below = window.innerHeight - anchor.bottom - gap - margin;
    var above = anchor.top - gap - margin;
    var height = menu.offsetHeight;
    var upward = height > below && above > below;
    var room = Math.max(upward ? above : below, 0);
    if (height > room) {
      menu.style.maxHeight = room + "px";
      height = menu.offsetHeight;
    }
    menu.style.top = (upward ? anchor.top - gap - height : anchor.bottom + gap) + "px";
    var width = menu.offsetWidth;
    var left = Math.min(anchor.right - width, window.innerWidth - width - margin);
    menu.style.left = Math.max(margin, left) + "px";
    menu.scrollTop = kept;
  }

  /* A scroll *inside* a panel moves nothing, so it re-places nothing. */
  function placeOpenPopovers(event) {
    var source = event && event.target;
    if (source && source.closest && source.closest("[data-uxfloat]")) {
      return;
    }
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
      /* A disclosure *inside* the panel — Dokumendid's `Muuda nime`, an
         opinion's `Võta tagasi` confirmation — changes the panel's height, so
         it is placed again: one that opened upwards must grow upwards, not
         down over its own trigger. `toggle` does not bubble; the capture
         phase still reaches the holder. */
      holder.addEventListener(
        "toggle",
        function (event) {
          if (event.target !== holder && holder.open) {
            place(holder);
          }
        },
        true
      );
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

  /* ---- Dokumendid `⋯` → `Muuda nime` -------------------------------------
   * The row's menu is an ordinary popover above; its first command is a nested
   * `<details>` that unfolds the title box inside the floating panel
   * (templates/matters/partials/document_rename_form.html). Two things here,
   * neither of which the command needs in order to work:
   *
   *  - opening it puts the cursor in the box with the old title selected, so
   *    choosing the command and typing the new name are one movement (the
   *    panel is placed again by the popover contract above, because it grew);
   *  - closing the menu by any route (Escape, a click outside, another row's
   *    `⋯`) folds the editor back and resets the box to the saved title, so the
   *    next `⋯` opens a menu, not a half-typed name nobody saved.
   *
   * A click inside the panel — in the box, on `Salvesta` — is inside the
   * `<details>` and never reaches the outside-click close above. Escape in the
   * box closes the editor (app.js, `details form`) and the menu with it (the
   * handler above), and focus lands on `⋯`, the control that opened it.
   */
  function bindDocumentRename(scope) {
    scope.querySelectorAll("details[data-docrename]").forEach(function (editor) {
      if (!once(editor, "DocRename")) {
        return;
      }
      var holder = editor.parentElement && editor.parentElement.closest("details[data-uxpopover]");
      editor.addEventListener("toggle", function () {
        if (!editor.open) {
          return;
        }
        var box = editor.querySelector("input[name=title]");
        if (box) {
          box.focus();
          box.select();
        }
      });
      if (holder) {
        holder.addEventListener("toggle", function () {
          if (holder.open) {
            return;
          }
          editor.open = false;
          var form = editor.querySelector("form");
          if (form) {
            form.reset();
          }
        });
      }
    });
  }

  /* ---- «Salvesta praegune filter vaatena», and «Teemaviide» ----------------
   * The view is the address. The control shows the current canonical URL and
   * offers to copy it; there is no stored view because there is nothing to
   * store — the link is the whole thing (matter_list.html).
   *
   * The same button copies a Teema's reference from the header, where the
   * value is plain text rather than a box (header.html, docs/adr/0150 §4): a
   * field is selected and its value copied, anything else has its text
   * selected and copied, so Ctrl+C works on the selection either way.
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
        var isField = typeof field.select === "function" && "value" in field;
        var text = isField ? field.value : (field.textContent || "").trim();
        if (isField) {
          field.select();
          field.setSelectionRange(0, field.value.length);
        } else {
          var range = document.createRange();
          range.selectNodeContents(field);
          var selection = window.getSelection();
          selection.removeAllRanges();
          selection.addRange(range);
        }
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
          navigator.clipboard.writeText(text).then(done, function () {});
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

  /* ---- `+ Ülevaade / uudis`: what the pasted address is, before the save ----
   * The server answers for the address in the box (`preview_website_overview`):
   * the kind its koda.ee path states, and the koda.ee page's own title. The
   * rules, all of which keep the person's own answers theirs:
   *
   * - an answer for an address that is no longer in the box is dropped — each
   *   request carries a sequence number and the server echoes the address;
   * - the title fills only an empty box, or one still holding the title this
   *   preview put there; a title somebody typed is never replaced;
   * - a known kind is chosen, because the save applies it anyway; an address
   *   that states none un-chooses only a kind this preview chose;
   * - nothing here is required: without scripting, or when the page cannot be
   *   read, the boxes are the boxes and the save decides (docs/adr/0142,
   *   amendment of 2026-10-07). */
  function bindPublicationPreview(scope) {
    scope.querySelectorAll("[data-publication-form]").forEach(function (form) {
      if (!once(form, "PublicationPreview") || !window.fetch || !window.FormData) {
        return;
      }
      var endpoint = form.getAttribute("data-preview-url");
      var address = form.querySelector("input[name=url]");
      var title = form.querySelector("input[name=overview_title]");
      var status = form.querySelector("[data-publication-status]");
      var token = form.querySelector("input[name=csrfmiddlewaretoken]");
      if (!endpoint || !address || !token) {
        return;
      }
      var asked = address.value.trim();
      var sequence = 0;
      var autoTitle = null;
      var autoKind = null;
      var timer = null;

      function say(text) {
        if (!status) {
          return;
        }
        status.textContent = text;
        status.hidden = !text;
      }

      function chooseKind(value) {
        var radios = form.querySelectorAll("input[name=kind]");
        Array.prototype.forEach.call(radios, function (radio) {
          if (value) {
            radio.checked = radio.value === value;
          } else if (autoKind && radio === autoKind) {
            radio.checked = false;
          }
        });
        autoKind = value ? form.querySelector("input[name=kind]:checked") : null;
      }

      function fillTitle(text) {
        if (!title) {
          return;
        }
        var current = title.value.trim();
        if (current && current !== autoTitle) {
          return;
        }
        title.value = text;
        autoTitle = text || null;
      }

      function preview() {
        var value = address.value.trim();
        if (value === asked) {
          return;
        }
        asked = value;
        sequence += 1;
        var mine = sequence;
        if (!/^https?:\/\/\S+$/i.test(value)) {
          chooseKind("");
          fillTitle("");
          say("");
          return;
        }
        say("Loen lehe andmeid…");
        var data = new FormData();
        data.append("url", value);
        data.append("csrfmiddlewaretoken", token.value);
        fetch(endpoint, { method: "POST", body: data, credentials: "same-origin" })
          .then(function (response) {
            return response.ok ? response.json() : null;
          })
          .then(function (answer) {
            if (mine !== sequence || !answer || answer.url !== address.value.trim()) {
              return;
            }
            chooseKind(answer.kind || "");
            if (answer.title) {
              fillTitle(answer.title);
            }
            say(answer.title_status === "failed" ? "Pealkirja ei õnnestunud lugeda — kirjuta see ise." : "");
          })
          .catch(function () {
            if (mine === sequence) {
              say("");
            }
          });
      }

      address.addEventListener("change", preview);
      address.addEventListener("paste", function () {
        window.setTimeout(preview, 0);
      });
      address.addEventListener("input", function () {
        window.clearTimeout(timer);
        timer = window.setTimeout(preview, 600);
      });
      Array.prototype.forEach.call(form.querySelectorAll("input[name=kind]"), function (radio) {
        radio.addEventListener("change", function () {
          autoKind = null;
        });
      });
    });
  }

  function bindAll(scope) {
    var root = scope && scope.querySelectorAll ? scope : document;
    bindPublicationPreview(root);
    bindQuickDates(root);
    bindAddPanels(root);
    bindFileDrop(root);
    bindWorkRows(root);
    bindExclusivePopovers(root);
    bindDocumentRename(root);
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

  /* A refused full-page POST — `Uus teema`, `Muuda teemat` — arrives as an
     ordinary render, so there is no swap to hang this off. Registered after
     `arriveAtNextStep` and on the same event, so that on the rare page which is
     both a refusal and a fragment arrival the refusal wins: the reader followed
     a link, but the save they pressed did not happen, and that is the more
     urgent of the two things to look at.

     On `load` rather than `DOMContentLoaded` for the reason spelled out above
     `arriveAtNextStep` — the browser's own fragment focusing steps run in
     between and would undo it. Pages with no error are untouched, because
     `focusFirstRefusal` returns without doing anything when it finds none. */
  window.addEventListener("load", function () {
    focusFirstRefusal(document);
  });

  document.body.addEventListener("htmx:afterSwap", function (event) {
    bindAll(event.target);
    focusFirstRefusal(event.target);
  });
})();
