"""Rules the browser suite holds itself to, checked without a browser.

Database-free and read from source, so they run in the FAST tier's contract
set (`ci_focused_selection.ALWAYS`) and fail a draft before any shard does.

1. **No bare summary click on a remembered block** (audit 2026-10-28, E-03).
   A remembered block (docs/adr/0194) is drawn the way its persona last left
   it, so a click on its `<summary>` *toggles* from a state the test did not
   choose. `e2e/conftest.py` resets every persona on sign-in, and asks for a
   state with `open_disclosure` / `close_disclosure` — or, where the toggle is
   the subject, `toggle_disclosure`, which asserts the state flipped.
2. **No new fixed sleeps** (E-06). `page.wait_for_timeout(…)` and
   `time.sleep(…)` are a guess at how long something takes; each one left is
   counted per file below (`INTENTIONAL_SLEEPS`). A new one
   fails here; replacing one with `expect(…)`, `wait_for_htmx`, `await_post`
   or `page.wait_for_function` means lowering its count.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
E2E = ROOT / "e2e"
TEMPLATES = ROOT / "templates"

# ---------------------------------------------------------------------------
# 1. Remembered blocks
# ---------------------------------------------------------------------------

#: `<details …>` opening tags, attributes and all.
_DETAILS_TAG = re.compile(r"<details\b[^>]*>", re.DOTALL)
_ID = re.compile(r'\bid="([^"{}]+)"')
_CLASS = re.compile(r'\bclass="([^"]+)"')

#: The conftest helpers that are allowed to click a summary: they decide from
#: the block's state, which is the whole point.
STATE_AWARE_HELPERS = frozenset({"_summary_of", "open_disclosure", "close_disclosure"})


_SUMMARY_TAG = re.compile(r"<summary\b[^>]*>", re.DOTALL)


_CSS_IDENT = re.compile(r"^-?[A-Za-z_][\w-]*$")


def _classes(tag: str) -> set[str]:
    """The literal class names on a tag; template expressions inside it are skipped."""
    classes: set[str] = set()
    for found in _CLASS.findall(tag):
        classes |= {c for c in found.split() if _CSS_IDENT.match(c)}
    return classes


def _template_blocks() -> tuple[set[str], set[str], set[str], set[str], set[str], set[str]]:
    """(remembered ids, remembered classes, other classes, and the same for summaries).

    A `<details>`'s summary is the first `<summary>` after its opening tag.
    """
    ids: set[str] = set()
    details_in: set[str] = set()
    details_out: set[str] = set()
    summary_in: set[str] = set()
    summary_out: set[str] = set()
    every_summary: set[str] = set()
    for template in TEMPLATES.rglob("*.html"):
        text = template.read_text(encoding="utf-8")
        for summary in _SUMMARY_TAG.findall(text):
            every_summary |= _classes(summary)
        for match in _DETAILS_TAG.finditer(text):
            tag = match.group(0)
            remembered = "{% disclosure" in tag
            following = _SUMMARY_TAG.search(text, match.end())
            summary_classes = _classes(following.group(0)) if following else set()
            if remembered:
                ids |= set(_ID.findall(tag))
                details_in |= _classes(tag)
                summary_in |= summary_classes
            else:
                details_out |= _classes(tag)
                summary_out |= summary_classes
    return ids, details_in, details_out, summary_in, summary_out, every_summary


def remembered_block_hooks() -> frozenset[str]:
    """CSS hooks that name a remembered block (or its summary) and nothing else.

    Read from the templates: every `<details>` carrying `{% disclosure … %}`
    contributes its id, each of its classes that no *un*remembered `<details>`
    also carries, and each class of its `<summary>` that no other summary
    carries (`.foldout__trigger`). Plus the attribute itself.
    """
    ids, details_in, details_out, summary_in, summary_out, _ = _template_blocks()
    hooks = {f"#{value}" for value in ids}
    hooks |= {f".{value}" for value in details_in - details_out}
    hooks |= {f".{value}" for value in summary_in - summary_out}
    hooks.add("[data-disclosure")
    return frozenset(hooks)


def summary_classes() -> frozenset[str]:
    """The classes of remembered blocks' own `<summary>`s: clicking one is a toggle.

    Only theirs — a chip that opens a *nested*, unremembered panel inside a
    remembered block (`.disclosure-chip`) is not a toggle of the block.
    """
    return frozenset(f".{value}" for value in _template_blocks()[3])


def _hook_pattern(hooks: frozenset[str]) -> re.Pattern[str]:
    # A class or id hook ends where the CSS name ends: `.uxdl` is not `.uxdl__head`.
    parts = [re.escape(hook) + (r"(?![\w-])" if hook[0] in "#." else "") for hook in hooks]
    return re.compile("|".join(sorted(parts)))


def _strings(node: ast.AST) -> list[str]:
    return [
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    ]


def _names(node: ast.AST) -> set[str]:
    return {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}


def bare_summary_clicks(
    source: str, hooks: frozenset[str], summaries: frozenset[str] = frozenset()
) -> list[tuple[int, str]]:
    """`(line, function)` of every summary click on a remembered block in `source`.

    Inside each function, a local variable carries the strings of the
    expression it was assigned from, and of the variables that expression
    names, so `window = page.locator("details.uxdl")` followed by
    `window.locator("summary").click()` is seen for what it is.
    """
    pattern = _hook_pattern(hooks)
    summary_pattern = _hook_pattern(summaries | {"summary"}) if summaries else None
    found: list[tuple[int, str]] = []
    tree = ast.parse(source)
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if function.name in STATE_AWARE_HELPERS:
            continue
        carried: dict[str, set[str]] = {}
        assignments = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Assign | ast.For | ast.With | ast.NamedExpr)
        ]
        # Two passes reach a variable assigned from a variable assigned from a literal.
        for _ in range(3):
            for node in assignments:
                if isinstance(node, ast.Assign):
                    targets, value = node.targets, node.value
                elif isinstance(node, ast.For):
                    targets, value = [node.target], node.iter
                elif isinstance(node, ast.NamedExpr):
                    targets, value = [node.target], node.value
                else:
                    continue
                strings = set(_strings(value))
                for name in _names(value):
                    strings |= carried.get(name, set())
                for target in targets:
                    for name in _names(target):
                        carried.setdefault(name, set()).update(strings)
        for node in ast.walk(function):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "click"
            ):
                continue
            receiver = node.func.value
            strings = set(_strings(receiver))
            for name in _names(receiver):
                strings |= carried.get(name, set())
                if "summary" in name:
                    strings.add("summary")
            # A click by the summary's own words inside a remembered block —
            # `block.get_by_text("Kuidas arvutatakse?").click()` — toggles it
            # exactly as a click on the `<summary>` element does.
            by_text = (
                isinstance(receiver, ast.Call)
                and isinstance(receiver.func, ast.Attribute)
                and receiver.func.attr == "get_by_text"
            )
            on_summary = any("summary" in text for text in strings) or (
                summary_pattern is not None
                and any(summary_pattern.search(text) for text in strings)
            )
            if not by_text and not on_summary:
                continue
            if any(pattern.search(text) for text in strings):
                found.append((node.lineno, function.name))
    return found


def test_the_remembered_block_hooks_are_found() -> None:
    """The scanner is only as good as the hooks it knows; pin the ones that matter."""
    hooks = remembered_block_hooks()
    for hook in (
        "#dokumendi-andmed",
        "#toodokumendid",
        "#seotud-arhiivikirjad",
        ".uxdl",
        ".foldout__trigger",
    ):
        assert hook in hooks, hook
    assert ".accordion__head" in summary_classes()
    # A class an ordinary disclosure also wears is not a hook: `.accordion` is
    # the release notes' day too, which is not remembered.
    assert ".accordion" not in hooks


@pytest.mark.parametrize(
    ("source", "flagged"),
    [
        ('def t(page):\n    page.locator("#dokumendi-andmed > summary").click()\n', True),
        (
            "def t(page):\n"
            '    windows = page.locator("details.uxdl")\n'
            '    summary = windows.first.locator("summary.uxdl__head")\n'
            "    summary.click()\n",
            True,
        ),
        (
            'def t(page):\n    d = page.locator("details.uxdl")\n    open_disclosure(d)\n',
            False,
        ),
        ('def t(page):\n    page.locator("details.relnotes__day summary").click()\n', False),
        ('def t(page):\n    page.locator(".uxdl__row a").click()\n', False),
        (
            'def t(page):\n    d = page.locator("details.uxdl").first\n'
            '    d.get_by_text("Kõik").click()\n',
            True,
        ),
        ('def t(page):\n    page.locator(".band .foldout__trigger").click()\n', True),
        (
            'def t(page):\n    w = page.locator("#dokumendi-andmed")\n'
            '    w.locator(".accordion__head").click()\n',
            True,
        ),
        ('def t(page):\n    page.locator("#ajajoon .accordion__head").click()\n', False),
    ],
)
def test_the_scanner_tells_a_bare_click_from_a_state_aware_one(source, flagged) -> None:
    hooks = frozenset({"#dokumendi-andmed", ".uxdl", ".foldout__trigger", "[data-disclosure"})
    summaries = frozenset({".accordion__head", ".foldout__trigger"})
    assert bool(bare_summary_clicks(source, hooks, summaries)) is flagged


def test_no_browser_test_clicks_the_summary_of_a_remembered_block() -> None:
    hooks = remembered_block_hooks()
    summaries = summary_classes()
    offences = [
        f"{path.relative_to(ROOT).as_posix()}:{line} in {function}"
        for path in sorted(E2E.glob("*.py"))
        for line, function in bare_summary_clicks(
            path.read_text(encoding="utf-8"), hooks, summaries
        )
    ]
    assert not offences, (
        "A remembered block (docs/adr/0194) arrives in the state its persona last "
        "left it, so a bare click on its summary toggles from a state this test did "
        "not choose. Use open_disclosure / close_disclosure, or toggle_disclosure "
        "where the toggle itself is under test (e2e/conftest.py):\n  " + "\n  ".join(offences)
    )


def test_every_sign_in_resets_the_remembered_blocks() -> None:
    """The deterministic start (E-03) is a property of `sign_in`, not of each test."""
    tree = ast.parse((E2E / "conftest.py").read_text(encoding="utf-8"))
    sign_in = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "sign_in"
    )
    called = {
        node.func.id
        for node in ast.walk(sign_in)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "reset_disclosures" in called


def test_the_landing_page_defaults_the_browser_suite_copies_are_the_registry_s() -> None:
    """`sign_in` redraws Minu asjad only when a block there is off its default;
    the defaults it compares with are copied, so they are held to the source."""
    from app.accounts.disclosures import DISCLOSURES

    copied = ast.literal_eval(
        next(
            node.value
            for node in ast.parse((E2E / "conftest.py").read_text(encoding="utf-8")).body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "LANDING_DISCLOSURE_DEFAULTS"
            and node.value is not None
        )
    )
    on_the_page = set(
        re.findall(
            r'{% disclosure "([^"]+)" %}',
            (TEMPLATES / "matters" / "my_work.html").read_text(encoding="utf-8"),
        )
    )
    assert set(copied) == on_the_page
    assert copied == {key: DISCLOSURES[key].default_open for key in on_the_page}


# ---------------------------------------------------------------------------
# 2. Fixed sleeps
# ---------------------------------------------------------------------------

#: Every fixed sleep left in the browser suite, per file. The count must match
#: exactly: a new sleep fails, and so does one removed without lowering its
#: number, so this list cannot drift from the code.
#:
#: E-06 replaced the riskiest kind — «click Salvesta, sleep, navigate away»,
#: where a short guess loses the write — in `test_correction_round_surfaces.py`
#: (8) and `test_note_concurrency.py` (9) with `await_post` and `expect`. The
#: entries marked *reviewed* were read and are intentional: there is no event to
#: wait for. The rest are inherited and unreviewed; replace them when the file
#: is next touched, and lower the count.
INTENTIONAL_SLEEPS: dict[str, int] = {
    # reviewed: one injects the slow reply (the route handler's own delay), the
    # other proves that reply, arriving late, does NOT overwrite — an absence.
    "e2e/test_followup_ux_round.py": 2,
    # reviewed: a TOTP step and an e-mail poll are clock-driven by design.
    "e2e/test_local_auth.py": 2,
    # reviewed: hover and focus tooltips; the bubble's appearance has no event.
    "e2e/test_matter_form_ux.py": 8,
    # reviewed: a viewport change settling before a layout read.
    "e2e/test_release_notes.py": 1,
    # reviewed: capture settle — a debounced side request or a pointer/transition
    # state has to land before the screenshot, and no DOM signal says it has.
    "e2e/test_ui_regression.py": 6,
    # inherited, not yet reviewed:
    "e2e/test_add_launcher_stability.py": 2,
    "e2e/test_document_file_links.py": 1,
    "e2e/test_external_position.py": 5,
    "e2e/test_focus_after_history_swaps.py": 3,
    "e2e/test_focus_visible_and_clear.py": 1,
    "e2e/test_header_search.py": 1,
    "e2e/test_integration_420px.py": 3,
    "e2e/test_lawyer_day_workflows.py": 1,
    "e2e/test_long_token_wrapping.py": 1,
    "e2e/test_navigation_qol.py": 4,
    "e2e/test_opinion_archive.py": 2,
    "e2e/test_process_strip.py": 3,
    "e2e/test_register_columns.py": 1,
    "e2e/test_register_search.py": 1,
    "e2e/test_select_commit.py": 3,
    "e2e/test_similar_matters_request.py": 1,
    "e2e/test_substantive_history.py": 1,
    "e2e/test_teema_kaik_accordion.py": 4,
    "e2e/test_teema_page_cleanup.py": 3,
    "e2e/test_teema_redesign.py": 1,
    "e2e/test_theme.py": 1,
    "e2e/test_theme_contrast.py": 1,
    "e2e/test_undated_next_actions.py": 1,
    "e2e/test_uus_teema_files.py": 3,
    "e2e/test_uus_teema_manual_first.py": 1,
    "e2e/test_uus_teema_menus.py": 2,
    "e2e/test_uus_teema_reading.py": 2,
    "e2e/test_uus_teema_refusal_and_restore.py": 1,
    "e2e/test_website_overview.py": 6,
    "e2e/test_work_surfaces_review.py": 1,
    "e2e/test_workspace_failures_and_drafts.py": 3,
}


def fixed_sleeps(source: str) -> int:
    """How many `….wait_for_timeout(…)` and `time.sleep(…)` calls a module makes."""
    count = 0
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr == "wait_for_timeout":
            count += 1
        elif (
            node.func.attr == "sleep"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "time"
        ):
            count += 1
    return count


def test_the_sleep_counter_counts_calls_not_words() -> None:
    source = (
        "import time\n"
        "def t(page):\n"
        "    page.wait_for_timeout(100)  # wait_for_timeout in a comment\n"
        "    time.sleep(0.1)\n"
        "    '''page.wait_for_timeout(5)'''\n"
    )
    assert fixed_sleeps(source) == 2


def test_no_new_fixed_sleeps_in_the_browser_suite() -> None:
    actual = {
        path.relative_to(ROOT).as_posix(): fixed_sleeps(path.read_text(encoding="utf-8"))
        for path in sorted(E2E.glob("*.py"))
    }
    actual = {path: count for path, count in actual.items() if count}
    assert actual == INTENTIONAL_SLEEPS, (
        "The browser suite's fixed sleeps changed. A new `wait_for_timeout` or "
        "`time.sleep` is a guess at a duration: wait for the condition instead "
        "(`expect(...)`, `wait_for_htmx`, `await_post`, `page.wait_for_function`). "
        "If you removed one, lower its count in INTENTIONAL_SLEEPS; if one is "
        "genuinely intentional (proving an absence, a hover, a debounce), add it "
        "with its reason.\n"
        f"  now:     {actual}\n  allowed: {INTENTIONAL_SLEEPS}"
    )


# ---------------------------------------------------------------------------
# 3. Clock-derived markup (E-02)
# ---------------------------------------------------------------------------

VISUAL_SUITE = E2E / "test_ui_regression.py"

#: How many hand-written selectors the visual suite masks today. The list is
#: how a clock value is kept out of a baseline, and every entry is a selector
#: that can silently stop matching. New clock-derived markup carries the bare
#: `data-clock` attribute instead — which the one `[data-clock]` entry already
#: covers — so this number may fall and may not rise. Converting an existing
#: class-based entry to `data-clock` means editing its template, then lowering
#: this.
CLOCK_MASK_CEILING = 33


def _module_list(path: Path, name: str) -> list[str]:
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return list(ast.literal_eval(node.value))
    raise AssertionError(f"{path.name} defines no {name}")


def test_data_clock_is_a_bare_attribute_everywhere() -> None:
    """`[data-clock]` is the one mask that covers it, so it carries no value to vary."""
    offences = [
        f"{template.relative_to(ROOT).as_posix()}:{number}"
        for template in sorted(TEMPLATES.rglob("*.html"))
        for number, line in enumerate(template.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"\bdata-clock\s*=", line)
    ]
    assert not offences, offences


def test_the_visual_suite_masks_every_data_clock_element() -> None:
    assert "[data-clock]" in _module_list(VISUAL_SUITE, "CLOCK_DEPENDENT")


def test_the_hand_written_clock_masks_do_not_grow() -> None:
    masks = _module_list(VISUAL_SUITE, "CLOCK_DEPENDENT")
    assert len(masks) <= CLOCK_MASK_CEILING, (
        f"CLOCK_DEPENDENT has {len(masks)} selectors, over {CLOCK_MASK_CEILING}. Mark the "
        "new clock-derived value with a bare `data-clock` attribute in its template instead "
        "of adding a selector: `[data-clock]` already masks it, and it cannot stop matching "
        "when a class is renamed."
    )
