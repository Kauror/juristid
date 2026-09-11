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

  /* ---- Uus teema: a "Muu" chip reveals its own text field ---------------
   * Progressive enhancement only. Without JavaScript the box is rendered by
   * the server in the state the answer puts it in and the form still works —
   * the server decides what "Muu" means, not this (Stage-2E.1 brief 20,
   * OIGUSAKT_UUS_TEEMA_DESIGN §16).
   *
   * Two fields now use the pattern, so it is written once. Valdkond's "Muu" is
   * a checkbox that is not a PolicyArea; Õigusakt's is a real vocabulary row.
   * That difference matters to the server and not at all to this: both are a
   * checkbox inside a known id that shows a known block.
   */
  [
    ["valdkond-muu", "valdkond-muu-tekst"],
    ["oigusakt-muu", "oigusakt-muu-tekst"]
  ].forEach(function (pair) {
    var chip = document.querySelector("#" + pair[0] + " input[type=checkbox]");
    var revealed = document.getElementById(pair[1]);
    if (!chip || !revealed) {
      return;
    }
    var syncOther = function () {
      revealed.hidden = !chip.checked;
      if (chip.checked) {
        var input = revealed.querySelector("input");
        if (input) {
          input.focus();
        }
      }
    };
    chip.addEventListener("change", syncOther);
    syncOther();
  });

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

        /* No badge in front of the name. Every row of this list carried the
           word TÕEND, which is what every row of it always is — a label that
           never varies tells the reader nothing and takes the first position
           on the line to do it. The evidence semantics are unchanged: each of
           these still becomes one Document with one immutable version through
           `app/documents/services.py`, and the Dokumendid tab is where a file's
           role is actually a question worth answering. */

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

    /* Taking a held file back off. The row carries the hidden `pending` key, so
       removing the row is what stops the next attempt resuming it — there is
       nothing to tell the server, because the key simply stops being posted.

       Enhancement, like the × beside a freshly chosen file: with scripting off
       neither control exists and both lists are still correct. */
    var heldList = document.getElementById("hoitud-failid");
    if (heldList) {
      heldList.addEventListener("click", function (event) {
        var button = event.target.closest ? event.target.closest("[data-drop-held]") : null;
        if (!button) {
          return;
        }
        var row = button.closest(".dropzone__file");
        if (row) {
          row.remove();
        }
        heldList.hidden = heldList.querySelectorAll(".dropzone__file").length === 0;
        fileInput.dispatchEvent(new Event("change"));
      });
    }

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
        if (!event.dataTransfer || !event.dataTransfer.files.length) {
          return;
        }
        if (!canDrop) {
          /* No DataTransfer to build with, so the browser's own assignment is
             the only thing available and a second drop replaces the first. */
          fileInput.files = event.dataTransfer.files;
          fileInput.dispatchEvent(new Event("change"));
          return;
        }
        /* Added to what is already there, not put in its place. Dropping a
           covering letter and then its annex is two gestures and one obvious
           intention; assigning the second FileList straight onto the input
           silently threw the first away. The same rebuild the × control uses,
           in the other direction. */
        var transfer = new DataTransfer();
        Array.prototype.slice.call(fileInput.files || []).forEach(function (file) {
          transfer.items.add(file);
        });
        Array.prototype.slice.call(event.dataTransfer.files).forEach(function (file) {
          transfer.items.add(file);
        });
        fileInput.files = transfer.files;
        fileInput.dispatchEvent(new Event("change"));
      });
    }

    /* ---- Uus teema: read the files while the form is still open ----------
     * Choosing a file uploads it before the Teema exists, the extraction
     * worker reads it behind the same scan gate as every other file, and what
     * the rules find appears on this form while somebody is still filling it
     * in (app/matters/intake_staging.py, docs/adr/0064).
     *
     * An enhancement, and it fails safely in both directions. With no
     * `fetch`, no `FormData` or no URLs on the dropzone there is no island at
     * all, and the input posts its files to `Loo teema` exactly as it always
     * did. When an upload fails the bytes stay in the input, so that ordinary
     * path still carries them — the island going wrong costs the suggestions,
     * never the file.
     *
     * The one rule that governs everything below: **a suggestion never
     * overwrites a person.** Extraction is asynchronous, so an answer can
     * arrive seconds after somebody has typed a title or ticked a sender, and
     * a control that is not empty — or that has been touched at all — is left
     * exactly as it is. The server says what it *would* fill; this decides
     * whether it still may (task §10).
     */
    var dropzone = fileInput.closest(".dropzone");
    var stageUrl = dropzone && dropzone.getAttribute("data-intake-url");
    var statusUrl = dropzone && dropzone.getAttribute("data-intake-status-url");
    var removeUrl = dropzone && dropzone.getAttribute("data-intake-remove-url");
    var createForm = fileInput.closest("form");
    if (stageUrl && statusUrl && removeUrl && createForm && window.FormData && window.fetch) {
      /* Slow enough to be a poll rather than a load, and bounded: after this
         many the page stops asking. Nothing is blocked while it waits — the
         file is already safe and `Loo teema` is already pressable — so the
         cost of stopping early is a suggestion, not a workflow (task §22). */
      var POLL_MS = 1200;
      var MAX_POLLS = 60;
      var polls = 0;
      var pollTimer = null;
      var abandoned = false;
      /* Consecutive failed status requests. One is a dropped packet; three in a
         row is a page that should stop pretending it is still being answered. */
      var failures = 0;
      var MAX_FAILURES = 3;

      /* The five controls a suggestion may fill.
         `title` is here and is not on `Muuda teemat`, which is the one rule
         that differs between the two surfaces. On a saved Matter nothing can
         tell a title a person wrote from one intake derived, so none is ever
         replaced; on this form there is no saved title, and this island can
         see the thing the record never could — whether the box is empty and
         whether anybody has been near it. `fill` below refuses on either
         count, so a machine title only ever lands in a box nobody has touched
         (app/matters/intake_suggestions/prefill.py, task §12). */
      var FILLABLE = [
        "title",
        "source_organisations",
        "response_deadline",
        "track",
        "policy_areas",
      ];
      /* Which of them the person has been near, and what we last wrote into
         each. The second is what lets a later answer replace an *earlier
         answer* while never replacing a person: a control still holding
         exactly what we put in it is still empty as far as they are
         concerned. */
      var touched = {};
      var autofilled = {};
      /* Files already sent. A `change` event is dispatched by the × control,
         by the held-file list and by our own clearing of the input, so
         "something changed" is not the question — "which of these has the
         server not got" is. */
      var sent = new WeakSet();

      var token = function () {
        var field = createForm.querySelector('input[name="intake"]');
        return field ? field.value : "";
      };
      var csrf = function () {
        var field = createForm.querySelector('input[name="csrfmiddlewaretoken"]');
        return field ? field.value : "";
      };
      var controlsFor = function (name) {
        return Array.prototype.slice.call(
          createForm.querySelectorAll('[name="' + name + '"], [name="' + name + '_other"]')
        );
      };
      var sameSet = function (left, right) {
        return (
          left.length === right.length &&
          left.every(function (value) {
            return right.indexOf(value) !== -1;
          })
        );
      };

      /* Touched, generously. Any real interaction anywhere inside the field a
         control lives in counts — a chip, its label, the date picker's own
         button — because the cost of being wrong in that direction is one
         suggestion nobody was offered, and the cost of being wrong in the
         other is somebody's typing disappearing. */
      var markTouched = function (event) {
        if (!event.isTrusted || !event.target.closest) {
          return;
        }
        var within = event.target.closest(".field, fieldset");
        if (!within) {
          return;
        }
        FILLABLE.forEach(function (name) {
          if (within.querySelector('[name="' + name + '"], [name="' + name + '_other"]')) {
            touched[name] = true;
          }
        });
      };
      ["input", "change", "click"].forEach(function (type) {
        createForm.addEventListener(type, markTouched, true);
      });

      /* «Kasuta» is a choice, and it is made outside the field it writes into,
         so the listener above cannot see it. Pressing it settles that field:
         a later answer must not take back what somebody has just accepted. */
      createForm.addEventListener(
        "click",
        function (event) {
          var use = event.target.closest ? event.target.closest("[data-suggest-for]") : null;
          if (use) {
            touched[use.getAttribute("data-suggest-for")] = true;
          }
        },
        true
      );

      /* The other control that answers the same question.

         Saatja and Adressaat each have two: the chips, which are the real form
         controls for every institution in the catalogue, and a hidden field
         carrying a body the catalogue does not hold — what `+` writes. They sit
         inside one picker, so the pairing is already in the document and does
         not need a second list here to keep in step.

         A typed name is an answer. It used to read as none, because emptiness
         was asked of the chips alone: a person who typed a sender, pressed `+`
         and was then refused by some other field came back to a page whose
         chips were all unticked, and the suggestion was applied over the top
         (R2-03, app/matters/intake_suggestions/analysis.py `has_sender`). */
      var answeredElsewhere = function (controls) {
        return controls.some(function (control) {
          var picker = control.closest ? control.closest("[data-orgfind]") : null;
          if (!picker) {
            return false;
          }
          var typedField = picker.querySelector("[data-orgfind-typed]");
          return !!(typedField && (typedField.value || "").trim());
        });
      };

      var fill = function (name, values) {
        if (touched[name]) {
          return;
        }
        var controls = controlsFor(name);
        if (!controls.length) {
          return;
        }
        if (answeredElsewhere(controls)) {
          return;
        }
        var boxes = controls.filter(function (control) {
          return control.type === "checkbox" || control.type === "radio";
        });
        var text = controls.filter(function (control) {
          return control.type !== "checkbox" && control.type !== "radio";
        })[0];
        var previous = autofilled[name] || [];

        if (boxes.length) {
          var checked = boxes
            .filter(function (box) {
              return box.checked && box.value !== "";
            })
            .map(function (box) {
              return box.value;
            });
          /* Empty, or holding exactly what this put there last time. Anything
             else is somebody's choice. */
          if (checked.length && !sameSet(checked, previous)) {
            return;
          }
          boxes.forEach(function (box) {
            var wanted = values.indexOf(box.value) !== -1;
            if (box.checked === wanted) {
              return;
            }
            /* Only ever tick what is proposed, or untick what this ticked. A
               box somebody else's answer left behind is not ours to clear. */
            if (wanted || previous.indexOf(box.value) !== -1) {
              box.checked = wanted;
              box.dispatchEvent(new Event("change", { bubbles: true }));
            }
          });
          autofilled[name] = values.slice();
          return;
        }

        if (!text) {
          return;
        }
        var current = (text.value || "").trim();
        if (current && current !== (previous[0] || "")) {
          return;
        }
        text.value = values[0] || "";
        text.dispatchEvent(new Event("input", { bubbles: true }));
        text.dispatchEvent(new Event("change", { bubbles: true }));
        autofilled[name] = values.slice();
      };

      var applyPrefill = function (scope) {
        var wanted = {};
        (scope || document).querySelectorAll("[data-prefill-for]").forEach(function (marker) {
          var name = marker.getAttribute("data-prefill-for");
          wanted[name] = wanted[name] || [];
          wanted[name].push(marker.getAttribute("data-prefill-value") || "");
        });
        /* Per field, not per value: Valdkonnad may propose three, and asking
           "is this control still empty" once per value would answer no after
           the first of them. */
        Object.keys(wanted).forEach(function (name) {
          fill(name, wanted[name]);
        });
      };

      /* One answer, two places. The staging routes render both halves and this
         puts each where it belongs by id — the file rows inside the dropzone,
         the suggestions above the fields they are about. */
      var applyFragment = function (source) {
        /* Either the answer's text or a document already parsed from it. The
           upload path parses first, because it has to look at what the server
           kept before deciding whether to empty the file input; the poll and
           the remove path have no such question and hand the text straight in.
           One function, so the two can never diverge about which element goes
           where. */
        var parsed =
          typeof source === "string"
            ? new DOMParser().parseFromString(source, "text/html")
            : source;
        ["intake-failid", "intake-panel"].forEach(function (id) {
          var incoming = parsed.getElementById(id);
          var existing = document.getElementById(id);
          if (incoming && existing) {
            existing.replaceWith(document.importNode(incoming, true));
          }
        });
        var panel = document.getElementById("intake-panel");
        if (panel) {
          bindSuggestionUse(panel);
          applyPrefill(panel);
        }
        /* The browser's own list and the chosen count are rebuilt from the
           input, which staging has just emptied, and from the staged rows that
           have taken its place. */
        fileInput.dispatchEvent(new Event("change"));
      };

      var uploading = function (on) {
        var notice = document.querySelector(".intakepanel__uploading");
        if (notice) {
          notice.hidden = !on;
        }
      };

      /* ---- The page stopped waiting, so it stops saying it is waiting ------
       *
       * This is the reported defect, and it is worth being precise about which
       * half of it lives here. A file staged on the deployed stack could never
       * be read at all, because nothing ever moved it past the malware gate
       * that used to stand in front of every parser (since removed) —
       * that is `app/documents/scanning.py`. But the browser gave up after
       * MAX_POLLS × POLL_MS ≈ 72 seconds and then simply `return`ed, leaving
       * the panel at `data-intake-state="reading"` with an animated spinner and
       * the words «Loen faili…» on screen. So somebody could sit in front of a
       * form for half an hour watching a page that had stopped asking anything
       * a minute in.
       *
       * A bounded poll loop is right and stays bounded. What changes is that
       * reaching the bound is an *outcome* with something to say, not a silent
       * exit: the spinner stops, the words become true, and the two things that
       * were always true — the file is staged, `Loo teema` works — are the ones
       * the person is left looking at (task §8).
       */
      var stall = function () {
        window.clearTimeout(pollTimer);
        var panel = document.getElementById("intake-panel");
        if (!panel || panel.getAttribute("data-intake-state") !== "reading") {
          return;
        }
        /* The same mechanism `Jätka ilma automaatse lugemiseta` uses, because
           it is the same situation reached by a different route: the page is no
           longer waiting for an answer. The words differ — one is a choice
           somebody made, the other is a thing that happened to them — and both
           are in the template with every other string on this page. */
        panel.setAttribute("data-intake-state", "stalled");
      };

      var schedule = function () {
        window.clearTimeout(pollTimer);
        var panel = document.getElementById("intake-panel");
        if (abandoned || !panel || panel.getAttribute("data-intake-state") !== "reading") {
          return;
        }
        if (polls >= MAX_POLLS) {
          stall();
          return;
        }
        pollTimer = window.setTimeout(function () {
          var current = token();
          if (!current) {
            return;
          }
          polls += 1;
          fetch(statusUrl + "?intake=" + encodeURIComponent(current), {
            credentials: "same-origin",
          })
            .then(function (response) {
              if (!response.ok) {
                throw new Error("status");
              }
              return response.text();
            })
            .then(function (html) {
              failures = 0;
              applyFragment(html);
              schedule();
            })
            .catch(function () {
              /* Still quietly — no alarm, nothing to act on — but no longer
                 silently *and* for ever. A dropped request is retried; a
                 sequence of them means the answer is not coming through this
                 page, and continuing to animate a spinner over it would be the
                 same lie by a different cause. */
              failures += 1;
              if (failures >= MAX_FAILURES) {
                stall();
                return;
              }
              schedule();
            });
        }, POLL_MS);
      };

      /* One list at a time. The browser's own preview is rebuilt from
         `input.files` on every change and the staged list is rebuilt from the
         server's answer, so while staging is working there must be exactly one
         of them or the two disagree for as long as an upload takes — long
         enough for somebody to press a × belonging to the list that is about
         to be replaced. The browser's is the one that goes, because the
         server's can say something it cannot: whether the file has been read.

         If staging ever fails, this stops suppressing and the browser's list
         comes back, because from then on it is the only true account of what
         `Loo teema` will receive. */
      var stagingWorks = true;
      var inFlight = 0;
      var hideChosenList = function () {
        if (!stagingWorks) {
          return;
        }
        /* Only while there is something to suppress it *for*. A batch the
           server refused outright stages nothing and clears nothing, so the
           browser's own preview is the only true account of what `Loo teema`
           will receive — and hiding it would show an empty file area over an
           input that still holds a file, which is the defect the held-upload
           work was about, inverted. */
        var staged = document.getElementById("intake-failid");
        if (!inFlight && !(staged && staged.querySelectorAll(".dropzone__file").length)) {
          return;
        }
        fileList.textContent = "";
        fileList.hidden = true;
      };

      var send = function (files) {
        var data = new FormData();
        files.forEach(function (file) {
          data.append("files", file);
        });
        data.append("csrfmiddlewaretoken", csrf());
        /* Read here rather than when the files were chosen. A second pick
           while the first upload is still in flight must join the session the
           first one created, not open a second — two sessions would mean the
           form carrying one identifier and half the files being filed. */
        var current = token();
        if (current) {
          data.append("intake", current);
        }
        return fetch(stageUrl, { method: "POST", body: data, credentials: "same-origin" })
          .then(function (response) {
            /* A 400 carries the same fragment with the refusal on it, so it is
               read rather than thrown: the page has to be able to say which
               file was not taken and why. */
            if (!response.ok && response.status !== 400) {
              throw new Error("stage");
            }
            return response.text();
          })
          .then(function (html) {
            inFlight -= 1;
            var parsed = new DOMParser().parseFromString(html, "text/html");
            var staged = parsed.getElementById("intake-failid");
            var kept = staged ? staged.querySelectorAll(".dropzone__file").length : 0;
            /* Emptied only once the server actually has something. A file input
               cannot be refilled by any page, so clearing it before the answer
               arrived — or when the answer was that nothing was taken — would
               be the one way to actually lose somebody's file. */
            if (kept) {
              fileInput.value = "";
            }
            polls = 0;
            failures = 0;
            abandoned = false;
            applyFragment(parsed);
            schedule();
          })
          .catch(function () {
            /* The bytes are still in the input, so `Loo teema` still carries
               them and every one of them still becomes a Document. What is
               lost is the reading, which is help rather than data — and the
               browser's own preview comes back, because from here on it is
               what the save will actually receive. */
            inFlight -= 1;
            stagingWorks = false;
            uploading(false);
            fileInput.dispatchEvent(new Event("change"));
          });
      };

      /* Uploads run one at a time. Dropping a covering letter and then its
         annex a moment later is two gestures somebody makes without waiting,
         and in parallel the second would be sent before the first had created
         the session for it to join. */
      var queue = Promise.resolve();

      fileInput.addEventListener("change", function () {
        /* Captured now, not when the turn comes: by then the input may have
           been emptied by the upload in front of this one. A `change` is also
           dispatched by the × control, by the held-file list and by this
           module itself, so "what has the server not got" is the question
           rather than "did something change". */
        var fresh = Array.prototype.slice.call(fileInput.files || []).filter(function (file) {
          return !sent.has(file);
        });
        if (!fresh.length) {
          hideChosenList();
          return;
        }
        fresh.forEach(function (file) {
          sent.add(file);
        });
        /* Counted here rather than when the request starts, and that is the
           whole of what makes the two lists safe. The queue runs `send` a
           microtask later at the earliest, so between the pick and the request
           there would otherwise be a window in which the browser's list is
           still showing rows whose × belongs to a list that is about to be
           replaced — long enough for somebody to press one, and long enough
           for a browser driver to. */
        inFlight += 1;
        uploading(true);
        hideChosenList();
        queue = queue.then(function () {
          return send(fresh);
        });
      });

      dropzone.addEventListener("click", function (event) {
        var button = event.target.closest ? event.target.closest("[data-intake-remove]") : null;
        if (!button) {
          return;
        }
        var current = token();
        if (!current) {
          return;
        }
        var data = new FormData();
        data.append("csrfmiddlewaretoken", csrf());
        data.append("intake", current);
        data.append("fail", button.getAttribute("data-intake-remove"));
        fetch(removeUrl, { method: "POST", body: data, credentials: "same-origin" })
          .then(function (response) {
            if (!response.ok) {
              throw new Error("remove");
            }
            return response.text();
          })
          .then(function (html) {
            /* The server decides what is left, what it now suggests and what
               `Loo teema` would file, all from one read — so the list and the
               suggestions cannot end up disagreeing about a file that is half
               gone (task §17). */
            applyFragment(html);
            schedule();
          })
          .catch(function () {});
      });

      createForm.addEventListener("click", function (event) {
        var skip = event.target.closest ? event.target.closest("[data-intake-skip]") : null;
        if (!skip) {
          return;
        }
        /* Not a cancellation: the file stays staged and still becomes evidence.
           This is for the person who can see the answer is not coming and would
           like the page to stop saying that it is (task §21). */
        abandoned = true;
        window.clearTimeout(pollTimer);
        var panel = document.getElementById("intake-panel");
        if (panel) {
          panel.setAttribute("data-intake-state", "abandoned");
        }
      });

      /* A refused save re-renders the page with its staged files and whatever
         had been found by then, so the island picks up where it left off
         rather than starting again. */
      var initial = document.getElementById("intake-panel");
      if (initial) {
        applyPrefill(initial);
        schedule();
      }
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

    /* Kaasamine: the explicit add action puts the caret in the form.

       `+ Lisa kaasamine` used to be a button inside the accordion's own
       <summary>, where a plain span is nothing but the disclosure's toggle, and
       this listener had to open the section, open the composer and move the
       focus (Kaasamine one-click §13).

       Since the 2026-09 refinement there is no section to open — Kaasamine is a
       section of the facts panel and is never shut — so the control is the
       disclosure's own <summary> and the browser opens it. What is left is the
       one thing the browser will not do: an explicit Add may take the focus,
       and opening a section may not (Kaasamine one-click §14).

       Bound to `toggle` rather than to a click, so it is right for the keyboard
       too: Enter on a <summary> opens the disclosure without a click event that
       could be intercepted. Nothing here is required for the form to work — with
       scripting off the disclosure still opens and the form is still inside it,
       which is why this moves focus and nothing else. */
    scope.querySelectorAll("[data-engagement-composer]").forEach(function (composer) {
      if (!once(composer, "EngagementAdd")) {
        return;
      }
      composer.addEventListener("toggle", function () {
        if (!composer.open) {
          return;
        }
        var form = composer.querySelector("form[data-engagement-add]");
        if (!form) {
          return;
        }
        /* Not `input` in general: every form here opens with a hidden CSRF
           token, and it is first in document order. */
        var field = form.querySelector("select, textarea, input:not([type=hidden])");
        if (field) {
          field.focus();
        }
      });
    });

    /* The composer's primary button says what the save will actually do. A
       button reading "Salvesta" that closes the file is the one thing a
       destructive-ish action must never look like (Teema redesign §15).

       It follows the *answers* now, not a confirmation checkbox: since the
       pilot found that unticking that box threw the whole closing section away
       (F-02), answering the section is what closes the Matter, and the button
       has to track exactly the same condition the server reads. It keeps
       tracking it when the panel is hidden again — Escape closes the disclosure
       without emptying it, and a hidden answer still closes the file, so the
       button is the one place that says so. */
    function closingAnswered(section) {
      var controls = section.querySelectorAll("input, select, textarea");
      for (var i = 0; i < controls.length; i += 1) {
        var control = controls[i];
        if (control.type === "checkbox" || control.type === "radio") {
          if (control.checked) {
            return true;
          }
        } else if (control.type === "file") {
          if (control.files && control.files.length) {
            return true;
          }
        } else if (String(control.value || "").trim() !== "") {
          return true;
        }
      }
      return false;
    }

    scope.querySelectorAll("[data-closing-section]").forEach(function (section) {
      if (!once(section, "Closing")) {
        return;
      }
      var form = section.closest("form");
      var submit = form ? form.querySelector("[data-composer-submit]") : null;
      if (!submit) {
        return;
      }
      var sync = function () {
        var closing = closingAnswered(section);
        submit.textContent = closing
          ? submit.getAttribute("data-label-closing")
          : submit.getAttribute("data-label-default");
        submit.classList.toggle("button--danger", closing);
      };
      /* Delegated to the section, so the `Muu` chips — which are added and
         removed after this runs — are covered without rebinding. */
      section.addEventListener("input", sync);
      section.addEventListener("change", sync);
      section.addEventListener("click", function () {
        window.setTimeout(sync, 0);
      });
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
     * control goes away rather than sitting there inviting a fabricated day.
     *
     * Looked for inside this control's own form rather than anywhere in the
     * scope. The Jõustumine form opens inline on the Matter page now, a few
     * hundred pixels below a composer carrying a period control of its own —
     * and a scope-wide lookup would hand the commencement form's kind radios
     * the power to hide the composer's «Oluline tähtaeg» fields. */
    var owner = (fields.closest && fields.closest("form")) || scope;
    var kindChooser = owner.querySelector("#joustumise-liik");
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
      /* Opt-in, and only Saatja asks for it.
       *
       * With `data-choicefilter-compact`, an empty box shows *nothing* rather
       * than everything: the list is a result area, not a wall down the middle
       * of the form. Ticked bodies are the exception and always show, which is
       * what keeps "what have I chosen" answerable without typing.
       *
       * The whole catalogue is still in the document, so with scripting off
       * this is an ordinary list of checkboxes and nothing is unreachable —
       * which is the only reason hiding it here is allowed at all. */
      var compact = holder.hasAttribute("data-choicefilter-compact");
      var apply = function () {
        var needle = box.value.trim().toLowerCase();
        var shown = 0;
        /* `.chip` is Uus teema's control, `.checkitem` the one every other
           surface still uses. One selector rather than two bindings, because
           the rule — hide what does not match, never hide what is ticked — is
           the same on both. */
        list.querySelectorAll(".chip, .checkitem").forEach(function (item) {
          var name = (item.textContent || "").trim().toLowerCase();
          var checked = item.querySelector("input:checked");
          /* A ticked choice never hides. Somebody who types after choosing
             should still be able to see — and untick — what they chose. */
          var hide = !checked && (needle === "" ? compact : name.indexOf(needle) === -1);
          item.hidden = hide;
          if (!hide) {
            shown += 1;
          }
        });
        if (compact) {
          /* An empty box would otherwise be an empty bordered panel, which
             reads as a control that has broken rather than one nobody has
             asked anything yet. */
          list.hidden = shown === 0;
        }
      };
      box.addEventListener("input", apply);
      list.addEventListener("change", apply);
      apply();
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
          if (radio.checked && field.value !== "") {
            field.value = "";
            /* Said out loud, because on `Uus teema` the box this empties is a
               hidden carrier with a chip standing for it — and a chip nobody
               took away is an answer the form no longer holds. Untrusted, so
               nothing reads it as somebody typing (`bindOrganisationPickers`). */
            field.dispatchEvent(new Event("input", { bubbles: true }));
          }
        });
      });
    });
  }

  /* ---- One control for «which institution?» -------------------------------
   *
   * `Uus teema` used to ask its two counterparty questions through three
   * controls each — a row of quick chips, a `<details>` reading «Vali
   * nimekirjast (N)» with a search box inside it, and a separate «Uus saatja» /
   * «Uus adressaat» text field somewhere else again. Three interactions to
   * learn, and the person had to decide which of them they were on before they
   * could type a letter.
   *
   * This is the one that replaced them:
   *
   *     Otsi kõigepealt olemasolevat. Kui seda ei ole, lisa sama välja kaudu uus.
   *
   * **Typing is not creating, and that boundary is the whole design.** The box
   * has no `name` and posts nothing. What it does is *find* — over the chips
   * already in the document, which are the real form controls for every
   * institution in the catalogue, so choosing a result ticks a control rather
   * than describing one and no request is made to select an existing body. Only
   * `+` writes, and what it writes is the typed name into `sender_name` /
   * `addressee_name` — the fields that have always carried a body the catalogue
   * does not hold. Nothing here creates an `Organisation`, at any point, under
   * any key (task §9, §25).
   *
   * **And the server still decides what a name means.** `+` on a spelling the
   * catalogue already holds selects that row here, because feedback a person
   * can see beats a surprise after the save — but that is *feedback*. The
   * decision is `app.organisations.services.resolve_organisation_name` inside
   * the save's own transaction: reuse an exact or alias match, create only a
   * genuinely new body, refuse a spelling that names two. The normalisation
   * below folds case and diacritics the way `normalize_for_matching` does so
   * that the same things look the same on screen; it is not a second definition
   * of identity and nothing is decided by it (task §10, §21, docs/adr/0073).
   *
   * Progressive enhancement throughout. With scripting off the shortlist is
   * still visible and tickable and the `<noscript>` block carries the rest of
   * the catalogue and the typed-name box; none of that is in the document here
   * (templates/matters/partials/organisation_picker.html).
   */

  /* Enough rows to choose from, few enough that the panel stays a list. Beyond
     this the query is the wrong length rather than the list being too short —
     refining brings the wanted row up, because the ranking puts exact and
     prefix matches first. */
  var ORGANISATION_RESULT_LIMIT = 20;

  /* Casefold, strip diacritics, collapse whitespace — `app.core.text
     .normalize_for_matching`, as far as a browser needs it. Comparison only:
     `data-aliases` arrives already normalised by the server, so this exists to
     put the *typed* text and the *rendered* label into the same shape. */
  function normalisedOrganisationName(value) {
    return (value || "")
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .replace(/\s+/g, " ")
      .trim();
  }

  function bindOrganisationPickers(scope) {
    (scope || document).querySelectorAll("[data-orgfind]").forEach(function (picker) {
      if (!once(picker, "OrgPicker")) {
        return;
      }
      var box = picker.querySelector("[data-orgfind-input]");
      var add = picker.querySelector("[data-orgfind-add]");
      var results = picker.querySelector(".orgfind__results");
      var chips = picker.querySelector("[data-orgfind-chips]");
      var typed = picker.querySelector("[data-orgfind-typed]");
      var status = picker.querySelector("[role=status]");
      if (!box || !add || !results || !results.id || !chips || !typed) {
        return;
      }

      /* Announced only now that the behaviour exists. Written into the template
         it would describe a listbox nothing can open, which is markup lying to
         a screen reader about what the page does (templates/base.html). */
      box.setAttribute("role", "combobox");
      box.setAttribute("aria-autocomplete", "list");
      box.setAttribute("aria-expanded", "false");
      box.setAttribute("aria-controls", results.id);
      results.setAttribute("role", "listbox");
      results.setAttribute("aria-label", "Asutused");

      var options = [];
      var active = -1;

      function chipLabel(chip) {
        var name = chip ? chip.querySelector(".chip__name") : null;
        return name ? name.textContent.trim().replace(/\s*×$/, "") : "";
      }

      /* Every institution this picker offers, read once.
       *
       * The membership cannot change — the whole catalogue is rendered, the
       * shortlist visibly and the rest `hidden` — so this is computed at bind
       * time. Order can and does change (`bindAddresseeDefault` moves a chosen
       * sender to the front of the Adressaat row), which is why results are
       * ranked from the query rather than read off the row. */
      var entries = [];
      var allChips = [];
      chips.querySelectorAll("label.chip").forEach(function (chip) {
        var input = chip.querySelector("input");
        if (!input) {
          return;
        }
        allChips.push({ chip: chip, input: input });
        if (!input.name || !input.value) {
          /* «Määramata» is a real radio with an empty value — it is what makes
             an addressee chosen by mistake unchoosable again — and the
             provisional chip posts nothing at all. Neither names an
             institution, so neither is searchable. */
          return;
        }
        var label = chipLabel(chip);
        entries.push({
          chip: chip,
          input: input,
          name: label,
          key: normalisedOrganisationName(label),
          aliases: (input.getAttribute("data-aliases") || "").split("|").filter(Boolean),
        });
      });

      function announce(text) {
        if (status) {
          status.textContent = text;
        }
      }

      /* Ranked the way task §6 asks, and the tie-break is the label so that two
         readers with the same query see the same list in the same order. */
      function score(entry, needle) {
        if (entry.key === needle) {
          return 0;
        }
        if (entry.aliases.indexOf(needle) !== -1) {
          return 1;
        }
        if (entry.key.indexOf(needle) === 0) {
          return 2;
        }
        if (entry.key.indexOf(needle) !== -1) {
          return 3;
        }
        var hit = entry.aliases.some(function (alias) {
          return alias.indexOf(needle) !== -1;
        });
        return hit ? 4 : -1;
      }

      function matching(needle) {
        var found = [];
        entries.forEach(function (entry) {
          var rank = score(entry, needle);
          if (rank >= 0) {
            found.push({ entry: entry, rank: rank });
          }
        });
        found.sort(function (a, b) {
          return a.rank - b.rank || a.entry.name.localeCompare(b.entry.name, "et");
        });
        return found.map(function (item) {
          return item.entry;
        });
      }

      /* The spelling that already *is* an institution, canonically or through a
         recorded alias. `+` on one of these selects the row rather than
         proposing the word — the same answer the server would reach, given
         early enough for somebody to see it (task §10). */
      function exactEntry(needle) {
        var found = null;
        entries.forEach(function (entry) {
          if (found) {
            return;
          }
          if (entry.key === needle || entry.aliases.indexOf(needle) !== -1) {
            found = entry;
          }
        });
        return found;
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
          box.setAttribute("aria-activedescendant", option.id);
          if (option.scrollIntoView) {
            option.scrollIntoView({ block: "nearest" });
          }
        } else {
          box.removeAttribute("aria-activedescendant");
        }
      }

      function closeResults() {
        results.hidden = true;
        results.textContent = "";
        options = [];
        active = -1;
        box.setAttribute("aria-expanded", "false");
        box.removeAttribute("aria-activedescendant");
      }

      function openResults(found) {
        results.textContent = "";
        options = [];
        active = -1;
        box.removeAttribute("aria-activedescendant");

        if (!found.length) {
          var empty = document.createElement("p");
          empty.className = "orgfind__empty";
          /* An option rather than loose text: the only valid child of a listbox
             is an option. Disabled, because there is nothing here to choose,
             and never pushed onto `options`, so the arrows skip it. */
          empty.setAttribute("role", "option");
          empty.setAttribute("aria-disabled", "true");
          empty.setAttribute("aria-selected", "false");
          empty.textContent = "Asutust ei leitud — lisa see nupuga +";
          results.appendChild(empty);
          announce("Asutust ei leitud");
        } else {
          found.slice(0, ORGANISATION_RESULT_LIMIT).forEach(function (entry, index) {
            var option = document.createElement("div");
            option.className = "orgfind__option";
            option.id = results.id + "-" + index;
            option.setAttribute("role", "option");
            option.setAttribute("aria-selected", "false");
            /* Reachable by the arrows and by the pointer, never by Tab: a
               listbox is one stop, and the `+` is the next one. */
            option.setAttribute("tabindex", "-1");
            /* textContent, so an institution named with a tag stays a name. */
            option.textContent = entry.name;
            option.addEventListener("mousedown", function (event) {
              /* Before the blur, so the box does not lose focus and close this
                 list out from under the click. */
              event.preventDefault();
            });
            option.addEventListener("click", function () {
              choose(entry);
            });
            results.appendChild(option);
            options.push(option);
          });
          announce(
            found.length > ORGANISATION_RESULT_LIMIT
              ? found.length + " sobivat asutust, näidatakse " + ORGANISATION_RESULT_LIMIT
              : found.length === 1
                ? "1 sobiv asutus"
                : found.length + " sobivat asutust"
          );
        }
        results.hidden = false;
        box.setAttribute("aria-expanded", "true");
      }

      /* What is on screen, from the query and from what has been answered.
       *
       * One rule for every chip, and the first half of it is task §8: a chip
       * that is an answer is never hidden. Not while somebody is searching for
       * the next sender, not because it was never in the shortlist, not because
       * the reader chose it rather than a person. The second half is §6: with a
       * query in the box the quick choices give way to the results. */
      function paint() {
        var needle = normalisedOrganisationName(box.value);
        allChips.forEach(function (item) {
          if (item.input.checked) {
            item.chip.hidden = false;
            return;
          }
          item.chip.hidden = needle
            ? true
            : item.chip.hasAttribute("data-orgfind-tail");
        });
        add.disabled = !box.value.trim();
        if (needle) {
          openResults(matching(needle));
        } else {
          closeResults();
          announce("");
        }
      }

      function clearQuery() {
        box.value = "";
        paint();
      }

      /* «A person answered this», said out loud.
       *
       * `bindAddresseeDefault` distinguishes a value it wrote from one somebody
       * gave, and it does so with `event.isTrusted` — which is exactly right for
       * a click on a radio and useless here, where a person's choice reaches the
       * control through this function. So the picker says so itself, from the
       * chip row, and only ever on a path a person started. */
      function declareAnswer() {
        chips.dispatchEvent(new CustomEvent("orgfind:answer", { bubbles: true }));
      }

      function choose(entry) {
        /* A radio unchecks its group by itself, which is what keeps Adressaat
           single-valued; a checkbox does not, which is what lets a Matter
           arrive from several bodies (task §7). */
        entry.input.checked = true;
        entry.chip.hidden = false;
        entry.input.dispatchEvent(new Event("change", { bubbles: true }));
        declareAnswer();
        clearQuery();
        box.focus();
        announce("Valitud: " + entry.name);
      }

      /* The chip standing for a body that does not exist yet.
       *
       * Built to match what the server renders for a refused save, so the two
       * states are one state: nameless, checked, dashed, and holding nothing
       * but the typed name. It posts nothing — the hidden field beside it is
       * what the server reads. */
      function syncProvisional() {
        var value = typed.value.trim();
        var chip = chips.querySelector("[data-orgfind-provisional]");
        if (!value) {
          if (chip) {
            chip.remove();
          }
          return;
        }
        if (!chip) {
          chip = document.createElement("label");
          chip.className = "chip chip--provisional";
          chip.setAttribute("data-orgfind-provisional", "");
          var input = document.createElement("input");
          input.type = "checkbox";
          input.className = "chip__input";
          input.checked = true;
          input.setAttribute("data-orgfind-provisional-input", "");
          var text = document.createElement("span");
          text.className = "chip__name";
          text.appendChild(document.createTextNode(value));
          var clear = document.createElement("span");
          clear.className = "chip__clear";
          clear.setAttribute("aria-hidden", "true");
          clear.textContent = "×";
          text.appendChild(clear);
          chip.appendChild(input);
          chip.appendChild(text);
          chips.insertBefore(chip, chips.firstChild);
          return;
        }
        var name = chip.querySelector(".chip__name");
        if (name && name.firstChild) {
          name.firstChild.nodeValue = value;
        }
        chip.querySelector("input").checked = true;
      }

      function setTyped(value) {
        typed.value = value;
        syncProvisional();
        /* Untrusted on purpose: `bindAddresseeDefault` listens here and must not
           read this as somebody typing into Adressaat — this *is* the picker,
           and what it means is said by `orgfind:answer` instead. */
        typed.dispatchEvent(new Event("input", { bubbles: true }));
        typed.dispatchEvent(new Event("change", { bubbles: true }));
      }

      function addTyped() {
        var raw = box.value.replace(/\s+/g, " ").trim();
        if (!raw) {
          return;
        }
        var known = exactEntry(normalisedOrganisationName(raw));
        if (known) {
          /* Already an institution, so this is that institution — never a
             second row spelled the same way (task §10). */
          choose(known);
          return;
        }
        setTyped(raw);
        declareAnswer();
        clearQuery();
        box.focus();
        announce("Lisatud uue asutusena: " + raw);
      }

      box.addEventListener("input", paint);

      box.addEventListener("keydown", function (event) {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          if (!options.length) {
            return;
          }
          event.preventDefault();
          var step = event.key === "ArrowDown" ? 1 : -1;
          var next = active + step;
          if (next < 0) {
            next = options.length - 1;
          }
          if (next >= options.length) {
            next = 0;
          }
          setActive(next);
          return;
        }
        if (event.key === "Escape") {
          if (!results.hidden) {
            event.preventDefault();
            closeResults();
          }
          return;
        }
        if (event.key === "Enter") {
          /* Never a submit, and never an add.
           *
           * This is a search box inside a long form, so an unguarded Enter
           * would file the Teema — and an Enter that fell through to «add new»
           * would file a duplicate institution under a name the person was
           * about to select from the list below. An existing result always
           * wins, and with nothing highlighted the keystroke does nothing at
           * all (task §19). */
          event.preventDefault();
          if (active >= 0 && options[active]) {
            options[active].click();
          }
        }
      });

      add.addEventListener("click", addTyped);

      /* Anything that changes what is ticked repaints, wherever it came from —
         a click on a chip, the intake reader's autofill, «Kasuta» on a
         suggestion, or `bindAddresseeDefault` answering Adressaat from Saatja.
         A body the reader chose is an answer like any other, and §8 says an
         answer is visible: the shortlist it is not in is not a reason to hide
         it (task §16). */
      picker.addEventListener("change", function (event) {
        var target = event.target;
        if (target && target.hasAttribute && target.hasAttribute("data-orgfind-provisional-input")) {
          if (!target.checked) {
            /* Letting go of the typed name is answering the question too. */
            setTyped("");
            declareAnswer();
          }
          paint();
          return;
        }
        paint();
      });

      /* The typed carrier is written from outside as well as from here — the
         server's own sender→addressee default reaches Adressaat through it. */
      typed.addEventListener("input", function () {
        syncProvisional();
        paint();
      });

      syncProvisional();
      paint();
    });
  }

  /* ---- Saatja is also the Adressaat, until somebody says otherwise -------
   *
   * A file arrives from X and is normally answered to X, so `Uus teema` fills
   * Adressaat from Saatja instead of asking the same question twice. Saatja and
   * Adressaat are two questions about *one* catalogue of institutions — the
   * same `Organisation` rows reached through two relations (docs/adr/0063) —
   * which is what makes the answer to one usable as the answer to the other.
   *
   * This replaces a rule rather than extending one. Until docs/adr/0069 the
   * sender was promoted to the front of the addressee choices and deliberately
   * *never* selected, on the argument that guessing a counterparty puts a fact
   * on the register nobody stated. That argument was not wrong about the risk;
   * it was wrong about the trade. Making the ordinary case free costs somebody
   * who is answering a different body one click of correction, and it is a
   * click they can see themselves making — the field says who it is answering,
   * and they change it.
   *
   * Four rules, and the second is the one that makes this safe to do at all:
   *
   * 1. **One unambiguous sender seeds it.** Whichever sender is chosen while
   *    nothing is seeded becomes the seed. A second sender added afterwards
   *    does not replace it, and when nothing is seeded and *several* senders
   *    are named there is no unambiguous body to answer, so Adressaat stays
   *    unanswered rather than guessed at.
   * 2. **A manual answer wins for ever.** The moment somebody touches Adressaat
   *    themselves, nothing here writes to it again — not a sender being added,
   *    removed, re-sorted, searched for or typed. That fact is posted in
   *    `addressee_is_manual`, so it survives a refused save and the server
   *    honours it too; it is never inferred from which chip happens to be
   *    first, because reordering is something this file legitimately does.
   * 3. **What was derived is taken back honestly.** Remove the sender the
   *    default came from and the default goes with it, rather than leaving a
   *    counterparty on the form that nothing on the page still supports.
   * 4. **One radio, moved — never a second one drawn.** The chosen sender's
   *    option is the same DOM node relocated to the front of the quick row, so
   *    the group still holds one control per organisation. A copy would post
   *    the same name twice and put the browser in charge of which one won.
   *
   * The server does all of this for the bound case, which is what a refused
   * save re-renders and what a browser with scripting off gets
   * (`app.matters.forms._default_addressee`). This is the half that has to work
   * before any round trip.
   */
  function bindAddresseeDefault(scope) {
    (scope || document).querySelectorAll("[data-counterparty-form]").forEach(function (form) {
      if (!once(form, "Counterparty")) {
        return;
      }
      var senderRow = form.querySelector("[data-sender-chips]");
      var quick = form.querySelector('[data-clears="addressee_organisation"]');
      var typed = form.querySelector("[data-sender-name]");
      var addresseeName = form.querySelector("[data-addressee-name]");
      var manualField = form.querySelector("[data-addressee-manual]");
      var summary = form.querySelector("[data-addressee-summary]");
      if (!senderRow || !quick) {
        return;
      }

      var senderInputs = function () {
        return form.querySelectorAll(
          'input[name="source_organisations"], input[name="source_organisations_other"]'
        );
      };
      var addresseeInputs = function () {
        return form.querySelectorAll('input[name="addressee_organisation"]');
      };

      var chipName = function (chip) {
        var name = chip ? chip.querySelector(".chip__name") : null;
        return name ? name.textContent.trim().replace(/\s*×$/, "") : "";
      };

      /* The label wrapping one addressee radio, wherever it currently lives —
         the visible chips or the part of the catalogue only the search reaches.
         Compared as a property rather than built into an attribute selector, so
         nothing here has to reason about escaping a value that came from the
         page. */
      var optionFor = function (value) {
        var found = null;
        addresseeInputs().forEach(function (radio) {
          if (!found && radio.value === value) {
            found = radio.closest(".chip");
          }
        });
        return found;
      };

      /* ---- the two states this has to tell apart -------------------------
       *
       * `manual` is somebody having answered Adressaat themselves, and it is
       * the only thing that stops everything below. It is restored from the
       * hidden field on load so that a refused save comes back knowing which of
       * the two it is looking at — a value on a re-rendered form is otherwise
       * indistinguishable from a value this script wrote a moment before the
       * person pressed the button.
       *
       * `seed` is the sender the current default came from, held as a token
       * rather than as a position: `{kind: "pk"|"typed", value}`. Position
       * cannot carry it, because answering Adressaat moves chips around.
       */
      var manual = !!(manualField && manualField.value);
      var seed = null;

      var sameToken = function (left, right) {
        return !!left && !!right && left.kind === right.kind && left.value === right.value;
      };

      /* The addressee radio offering one body *by name*, if the catalogue holds
         it. The typed field is for a body that does not exist yet, but people
         put names into it that do — and a spelling the catalogue already holds
         should answer with the row rather than with the word. */
      var optionValueNamed = function (name) {
        var found = null;
        addresseeInputs().forEach(function (radio) {
          if (found === null && radio.value && chipName(radio.closest(".chip")) === name) {
            found = radio.value;
          }
        });
        return found;
      };

      /* Every sender this form currently names: the ticked bodies, plus the one
         the picker's `+` has proposed as new. A typed name the catalogue does
         not hold has no primary key and is carried by its spelling — which is
         exactly what `addressee_name` posts, and what the server resolves
         against the same catalogue inside the save's own transaction. */
      var senderTokens = function () {
        var tokens = [];
        senderInputs().forEach(function (input) {
          if (input.checked) {
            tokens.push({ kind: "pk", value: input.value, name: chipName(input.closest(".chip")) });
          }
        });
        var name = typed ? typed.value.trim() : "";
        if (name) {
          var known = optionValueNamed(name);
          tokens.push(
            known
              ? { kind: "pk", value: known, name: name }
              : { kind: "typed", value: name, name: name }
          );
        }
        return tokens;
      };

      /* Adressaat's own typed field, written and taken back.
       *
       * A body being named for the first time has no primary key — there is no
       * row until `Loo teema` creates one — so the same name becomes the
       * addressee through the control that already exists for exactly that.
       * The event is what makes the Adressaat picker draw or drop its
       * provisional chip; the picker owns that chip, and this owns the value
       * (`bindOrganisationPickers`, task §14).
       *
       * Untrusted by construction, which is how the picker and the listener
       * below both know the person did not type it. */
      var setTypedAddressee = function (value) {
        if (!addresseeName || addresseeName.value === value) {
          return;
        }
        addresseeName.value = value;
        addresseeName.dispatchEvent(new Event("input", { bubbles: true }));
      };

      /* The chosen senders, at the front of the Adressaat chips.
       *
       * Ordering, and since docs/adr/0069 it is ordering with a job rather than
       * a suggestion: the body that has just become the addressee has to be one
       * of the chips, because the chips are what somebody sees when they open
       * Adressaat to check. A default left among the bodies only the search
       * reaches would be an answer hidden behind a query nobody would think to
       * type. `hidden = false` is the other half of that: those entries arrive
       * out of sight, and an answer is never out of sight (task §8). */
      var promote = function () {
        var chosen = [];
        senderInputs().forEach(function (input) {
          if (input.checked) {
            chosen.push(input);
          }
        });
        /* By the label the person reads, so several senders move in the order
           the server would also put them in rather than in whichever order the
           two checkbox groups happen to appear in the document. */
        chosen.sort(function (a, b) {
          return chipName(a.closest(".chip")).localeCompare(chipName(b.closest(".chip")), "et");
        });
        /* Inserted in reverse so that repeated `insertBefore(first)` leaves
           them in `chosen` order, and after the provisional chip if there is
           one — a body the catalogue does not hold yet is the one the person is
           in the middle of naming. */
        var provisional = quick.querySelector("[data-orgfind-provisional]");
        for (var index = chosen.length - 1; index >= 0; index -= 1) {
          var option = optionFor(chosen[index].value);
          if (!option) {
            continue;
          }
          var anchor =
            provisional && provisional.parentNode === quick
              ? provisional.nextSibling
              : quick.firstChild;
          if (option !== anchor) {
            quick.insertBefore(option, anchor);
          }
          option.hidden = false;
        }
      };

      /* What the collapsed disclosure says: «Adressaat», or «Adressaat · X».
         The server renders the same sentence for a bound form; this keeps it
         true while somebody is still filling the form in. */
      var updateSummary = function () {
        if (!summary) {
          return;
        }
        var name = "";
        addresseeInputs().forEach(function (radio) {
          if (radio.checked && radio.value) {
            name = chipName(radio.closest(".chip"));
          }
        });
        if (!name && addresseeName) {
          name = addresseeName.value.trim();
        }
        summary.textContent = name ? " · " + name : "";
      };

      /* Write the derived answer, or take it back.
       *
       * Only ever reached while `manual` is false, which is what makes it safe
       * to overwrite whatever is in the controls: everything there was put
       * there by this function, or by the server's identical rule. */
      var applyDefault = function () {
        if (seed && seed.kind === "typed") {
          addresseeInputs().forEach(function (radio) {
            radio.checked = false;
          });
          setTypedAddressee(seed.value);
          return;
        }
        setTypedAddressee("");
        /* `Määramata` is a real radio with an empty value, and it is what "no
           answer" looks like — so clearing the default means selecting it,
           never leaving the group with nothing checked. */
        addresseeInputs().forEach(function (radio) {
          radio.checked = seed ? radio.value === seed.value : radio.value === "";
        });
      };

      var refresh = function () {
        promote();
        if (!manual) {
          var tokens = senderTokens();
          if (
            seed &&
            !tokens.some(function (token) {
              return sameToken(token, seed);
            })
          ) {
            /* The sender the answer came from is gone, so the answer goes with
               it rather than standing on nothing (§8). */
            seed = null;
          }
          if (!seed && tokens.length === 1) {
            seed = tokens[0];
          }
          applyDefault();
        }
        updateSummary();
      };

      /* What the form arrived holding, before anybody touches it.
       *
       * A bound form the server defaulted comes back with the answer in place
       * and `addressee_is_manual` unset, and this is where that becomes a seed
       * again — found by matching the answer against the senders, which is
       * exact, rather than by reading which chip is first, which is not. An
       * answer no sender explains was given by a person, on this form or on the
       * one before the refusal, and it is theirs. */
      var adopt = function () {
        if (manual) {
          return;
        }
        var answer = "";
        addresseeInputs().forEach(function (radio) {
          if (radio.checked && radio.value) {
            answer = radio.value;
          }
        });
        var name = addresseeName ? addresseeName.value.trim() : "";
        senderTokens().forEach(function (token) {
          if (seed) {
            return;
          }
          if (token.kind === "pk" && answer && token.value === answer) {
            seed = token;
          }
          if (token.kind === "typed" && !answer && name && token.value === name) {
            seed = token;
          }
        });
        if (!seed && (answer || name)) {
          takeOver();
        }
      };

      function takeOver() {
        if (manual) {
          return;
        }
        manual = true;
        seed = null;
        if (manualField) {
          manualField.value = "1";
        }
      }

      form.addEventListener("change", function (event) {
        var target = event.target;
        if (!target || !target.name) {
          return;
        }
        if (
          target.name === "source_organisations" ||
          target.name === "source_organisations_other"
        ) {
          refresh();
          return;
        }
        if (target.name === "addressee_organisation" && event.isTrusted) {
          takeOver();
          updateSummary();
        }
      });

      /* «A person answered Adressaat», said by the picker.
       *
       * `event.isTrusted` is the right test for a click on a radio and the
       * wrong one for the unified picker, where choosing a search result,
       * pressing `+` and letting go of a provisional chip all reach the control
       * through script. So the picker announces a person-driven answer from its
       * own chip row, and only that row's announcement counts here — the Saatja
       * picker fires the same event and it must feed this default rather than
       * override it (task §15, `bindOrganisationPickers`). */
      form.addEventListener("orgfind:answer", function (event) {
        if (event.target !== quick) {
          return;
        }
        takeOver();
        updateSummary();
      });

      if (typed) {
        typed.addEventListener("input", refresh);
      }
      if (addresseeName) {
        addresseeName.addEventListener("input", function (event) {
          /* A person typing into the `<noscript>` box — or, with scripting on,
             nothing at all, because everything that writes here does so
             untrusted and says what it meant through the event above. */
          if (event.isTrusted) {
            takeOver();
          }
          updateSummary();
        });
      }

      promote();
      adopt();
      updateSummary();
    });
  }

  /* ---- A disclosure holding a ticked choice opens itself ------------------
   * `Vali nimekirjast` is closed by default, which is right on arrival and
   * wrong after a refused save: the catalogue behind it may hold the body the
   * person ticked, and a form that comes back with an error and their answer
   * hidden looks like a form that discarded it. The value was always posted;
   * this is only about being able to see it.
   *
   * `data-stay-closed` is the exception, and it exists because the rule above
   * assumes a ticked choice is somebody's answer. Adressaat's own disclosure
   * holds one on the *ordinary* visit now — the sender fills it in — so
   * applying this there would unfold a section every time the page answered a
   * question on the person's behalf, which is the opposite of what defaulting
   * it was for. That disclosure opens for an error and for a click, and for
   * nothing else (docs/adr/0069, templates/matters/matter_create.html).
   */
  function bindOpenChosenDetails(scope) {
    (scope || document).querySelectorAll("details.chipdetails").forEach(function (holder) {
      if (!once(holder, "OpenChosen")) {
        return;
      }
      if (holder.hasAttribute("data-stay-closed")) {
        return;
      }
      if (holder.querySelector("input:checked")) {
        holder.open = true;
      }
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
         — or one element's id, which is how the file input is counted.
         The `_other` twin is included for the same reason `bindSuggestionUse`
         looks it up: Saatja is one logical set split across two fields because
         a checkbox group cannot be rendered in two places without being two
         fields, and a count that read only the shortlist said «1 valitud» over
         two ticked bodies (app/matters/forms.py). */
      var byName = form.querySelectorAll(
        'input[name="' + key + '"], input[name="' + key + '_other"]'
      );
      var single = document.getElementById(key);
      var sources = byName.length ? Array.prototype.slice.call(byName) : single ? [single] : [];
      if (!sources.length) {
        return;
      }
      /* Files the server is holding count as chosen, because they are: the
         next save files them. Counting only `input.files` would have said
         "1 valitud" over a list of two rows — and once `Uus teema` began
         uploading a chosen file straight away, `input.files` is empty on the
         ordinary path and the count would have read nothing at all.

         Two lists, looked up per sync rather than once: both are replaced
         wholesale, the staged one on every answer from the staging routes
         (static/js/app.js above, app/documents/pending.py). */
      var lists = key === "id_files" ? ["hoitud-failid", "intake-failid"] : [];
      var sync = function () {
        var count = single && sources[0] === single
          ? (single.files || []).length
          : sources.filter(function (input) {
              return input.checked && input.value !== "";
            }).length;
        lists.forEach(function (id) {
          var list = document.getElementById(id);
          if (list) {
            count += list.querySelectorAll(".dropzone__file").length;
          }
        });
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

  /* ---- «Kasuta» on Dokumendist leitud --------------------------------------
   * A suggestion the person chooses is written into the real form control
   * beside it — the title box, the deadline box, the Menetlusliik radio, a
   * Valdkond or Kellelt checkbox — and nothing else. The control is what is
   * submitted and validated, exactly as it is when typed; the button stores
   * nothing of its own.
   *
   * Progressive enhancement: with scripting off the suggestion and its
   * evidence are still on the page and the value is still typed by hand.
   * A checkbox group may be rendered in two places (the frequent chips and
   * the long tail behind «Vali nimekirjast»), so a value is looked up by name
   * and by the `_other` twin (app/matters/forms.py).
   */
  function bindSuggestionUse(scope) {
    (scope || document).querySelectorAll("[data-suggest-for]").forEach(function (button) {
      if (!once(button, "SuggestionUse")) {
        return;
      }
      var form = button.closest("form");
      if (!form) {
        return;
      }
      var name = button.getAttribute("data-suggest-for");
      var value = button.getAttribute("data-suggest-value") || "";
      var controls = Array.prototype.slice.call(
        form.querySelectorAll('[name="' + name + '"], [name="' + name + '_other"]')
      );
      var boxes = controls.filter(function (control) {
        return control.type === "checkbox" || control.type === "radio";
      });
      var text = controls.filter(function (control) {
        return control.type !== "checkbox" && control.type !== "radio";
      })[0];
      var chosen = function () {
        if (boxes.length) {
          return boxes.some(function (box) {
            return box.value === value && box.checked;
          });
        }
        return !!text && text.value.trim() === value;
      };
      var sync = function () {
        var on = chosen();
        button.classList.toggle("is-selected", on);
        button.setAttribute("aria-pressed", on ? "true" : "false");
      };
      button.addEventListener("click", function () {
        var target = null;
        if (boxes.length) {
          boxes.forEach(function (box) {
            if (box.value !== value) {
              return;
            }
            target = box;
            if (!box.checked) {
              box.checked = true;
              box.dispatchEvent(new Event("change", { bubbles: true }));
            }
            /* A long-tail chip sits behind a closed disclosure; open it so
               the tick is visible where it was made. */
            var details = box.closest("details");
            if (details) {
              details.open = true;
            }
          });
        } else if (text) {
          target = text;
          text.value = value;
          text.dispatchEvent(new Event("input", { bubbles: true }));
          text.dispatchEvent(new Event("change", { bubbles: true }));
        }
        sync();
        if (target && target.focus) {
          target.focus({ preventScroll: false });
        }
      });
      controls.forEach(function (control) {
        control.addEventListener("change", sync);
        control.addEventListener("input", sync);
      });
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
    bindOrganisationPickers(document);
    bindAddresseeDefault(document);
    bindOpenChosenDetails(document);
    bindChipCounts(document);
    bindStageHelp(document);
    bindRequiredAction(document);
    bindSuggestionUse(document);
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
    bindOrganisationPickers(event.target.querySelector ? event.target : document);
    bindAddresseeDefault(event.target.querySelector ? event.target : document);
    bindOpenChosenDetails(event.target.querySelector ? event.target : document);
    bindChipCounts(event.target.querySelector ? event.target : document);
    bindStageHelp(event.target.querySelector ? event.target : document);
    bindRequiredAction(event.target.querySelector ? event.target : document);
    bindSuggestionUse(event.target.querySelector ? event.target : document);
    bindPersonaMenu(event.target.querySelector ? event.target : document);
  });
})();
