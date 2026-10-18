"""Fix-loop iteration 26 tests — WORLDLE AREA (evidence-grounded in
ANALYSIS23, run 39097177159, RC-new-1).

Frame/log-proven defect: click 1 hit the Area container DIV at its
centre (960, 288) — the 6px gap between the Kuwait card button
(x=709 w=248, ends 957) and the Qatar card button (x=963 w=248).
The retry hit the label-bar DIV ('Kuwait' text bar, 240x26,
centre (833, 441)), not interactive. The real card element —
BUTTON 'Image Quiz Item' (silhouette button) — was NEVER clicked.
Post-click probe: selected=False, all options signals=[].

Deeper root cause (from the code, not just the log): the label
pill is NEITHER a sibling NOR a descendant of the silhouette
BUTTON, so name matching can NEVER match the button (its text is
the generic 'Image Quiz Item') — the button click fell through
to the DIV fallback and hit the label bar. And the container DIV
matched the card matcher via a descendant-label scan.

Fixes covered here:
1. The button click matches the BUTTON by its descendant img src
   (2-letter basename kw.svg / qa.svg IS the country code);
   detail records match=src. Name matching stays as fallback.
2. The card matcher can no longer match the container DIV
   (no interactive descendant, exactly one img, own img, not
   the prompt text) or the label-bar DIV (no img).
3. The button-click DIV fallback excludes bare label pills
   (must be >=60px tall; the label bar is 26px).
4. The probe merges a label pill sitting below a generic-named
   button into that button (no 4-entries-for-2-cards), and the
   Python dedup keeps the imaged card per name.
5. Selection is proven by a before/after fingerprint diff of
   the target BUTTON (class / computed border / box-shadow ring
   / background / aria / data-selected, + submit-control
   appearance/enabling). A proven fingerprint diff skips the
   retry click (a second click could toggle selection off) and
   satisfies the submit gate. No diff + no probe selection =
   the iter-25 gate still blocks submit.
6. Card-first + button-fallback ordering, view-flip -> partial
   no-submit, GDP buttons-first + submit-anyway (no gate leak)
   are all preserved.

All tests are synthetic/local: no network, no browser, no CI run.
"""
import inspect
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner as R


def _dbg_recorder(logs):
    def _rec(gid, msg):
        logs.append(f"[{gid}] {msg}")
    return _rec


# Probe options as the iter-25 run produced them: 2 silhouette
# buttons (generic names in the raw probe; renamed by src
# resolution in production — the fake carries the resolved
# names, the dedup step is what is under test) + 2 label pills.
_BTN_KUWAIT = {"name": "Kuwait", "x": 833, "y": 300,
               "has_img": True,
               "rect": {"x": 709, "y": 176, "w": 248, "h": 248},
               "text": "Image Quiz Item", "aria": "", "alt": "",
               "title": "",
               "src": "https://cdn-assets.teuteuf.fr/country-shapes/kw.svg"}
_LABEL_KUWAIT = {"name": "Kuwait", "x": 833, "y": 441,
                 "has_img": False,
                 "rect": {"x": 713, "y": 428, "w": 240, "h": 26},
                 "text": "Kuwait", "aria": "", "alt": "",
                 "title": "", "src": ""}
_BTN_QATAR = {"name": "Qatar", "x": 1087, "y": 300,
              "has_img": True,
              "rect": {"x": 963, "y": 176, "w": 248, "h": 248},
              "text": "Image Quiz Item", "aria": "", "alt": "",
              "title": "",
              "src": "https://cdn-assets.teuteuf.fr/country-shapes/qa.svg"}
_LABEL_QATAR = {"name": "Qatar", "x": 1087, "y": 441,
                "has_img": False,
                "rect": {"x": 963, "y": 428, "w": 240, "h": 26},
                "text": "Qatar", "aria": "", "alt": "",
                "title": "", "src": ""}


# ------------------------------------------- pure helpers

class TestSrcCodeIter26(unittest.TestCase):
    def test_kw_and_qa_basename(self):
        self.assertEqual(R._worldle_area_src_code(
            "https://cdn-assets.teuteuf.fr/country-shapes/kw.svg"), "kw")
        self.assertEqual(R._worldle_area_src_code(
            "https://cdn-assets.teuteuf.fr/country-shapes/qa.svg"), "qa")

    def test_query_string_and_case(self):
        self.assertEqual(R._worldle_area_src_code(
            "/shapes/KW.svg?v=3"), "kw")

    def test_no_code(self):
        self.assertEqual(R._worldle_area_src_code(""), "")
        self.assertEqual(R._worldle_area_src_code(None), "")
        self.assertEqual(R._worldle_area_src_code(
            "https://example.com/shapes/kwt.svg"), "")
        self.assertEqual(R._worldle_area_src_code(
            "https://example.com/no-image-here"), "")


class TestDedupIter26(unittest.TestCase):
    def test_four_entries_for_two_cards_collapse_to_two(self):
        out = R._s_worldle_dedup_comparison_options(
            [dict(_BTN_KUWAIT), dict(_LABEL_KUWAIT),
             dict(_BTN_QATAR), dict(_LABEL_QATAR)])
        self.assertEqual([o["name"] for o in out],
                         ["Kuwait", "Qatar"])
        # The kept entries are the imaged cards, not the pills.
        self.assertTrue(all(o.get("has_img") for o in out))
        self.assertTrue(all(o.get("src") for o in out))

    def test_label_pill_only_group_keeps_first(self):
        # GDP-style text buttons carry no image: same-name
        # duplicates still collapse, first kept.
        out = R._s_worldle_dedup_comparison_options(
            [dict(_LABEL_KUWAIT), dict(_LABEL_KUWAIT)])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["name"], "Kuwait")

    def test_empty_and_non_dicts(self):
        self.assertEqual(R._s_worldle_dedup_comparison_options([]), [])
        self.assertEqual(
            R._s_worldle_dedup_comparison_options([None, "x"]), [])


class TestFingerprintDiffIter26(unittest.TestCase):
    def _fp(self, kw_cls="card", kw_shadow="none", submit=()):
        return {"buttons": [
            {"code": "kw", "name": "Image Quiz Item", "cls": kw_cls,
             "border": "rgb(0, 0, 0)", "shadow": kw_shadow,
             "bg": "rgb(255, 255, 255)", "aria_selected": "",
             "aria_pressed": "", "aria_checked": "",
             "data_selected": ""},
            {"code": "qa", "name": "Image Quiz Item", "cls": "card",
             "border": "rgb(0, 0, 0)", "shadow": "none",
             "bg": "rgb(255, 255, 255)", "aria_selected": "",
             "aria_pressed": "", "aria_checked": "",
             "data_selected": ""}],
            "submit": list(submit)}

    def test_identical_is_no_selection(self):
        changed, detail = R._worldle_area_fingerprint_changed(
            self._fp(), self._fp(), "kw")
        self.assertFalse(changed)
        self.assertIn("no fingerprint change", detail)

    def test_class_and_ring_change_is_selection(self):
        changed, detail = R._worldle_area_fingerprint_changed(
            self._fp(), self._fp(kw_cls="card ring-2",
                                 kw_shadow="0 0 0 2px rgb(59, 130, 246)"),
            "kw")
        self.assertTrue(changed)
        self.assertIn("cls", detail)
        self.assertIn("shadow", detail)

    def test_other_card_changing_does_not_count(self):
        before = self._fp()
        after = self._fp()
        after["buttons"][1]["cls"] = "card ring-2"
        changed, _ = R._worldle_area_fingerprint_changed(
            before, after, "kw")
        self.assertFalse(changed)

    def test_submit_control_enabling_is_selection(self):
        changed, detail = R._worldle_area_fingerprint_changed(
            self._fp(submit=[{"text": "submit", "disabled": True}]),
            self._fp(submit=[{"text": "submit", "disabled": False}]),
            "kw")
        self.assertTrue(changed)
        self.assertIn("submit control", detail)

    def test_missing_snapshot_target_or_code_is_no_evidence(self):
        self.assertFalse(R._worldle_area_fingerprint_changed(
            {}, self._fp(), "kw")[0])
        self.assertFalse(R._worldle_area_fingerprint_changed(
            self._fp(), {"buttons": [], "submit": []}, "kw")[0])
        self.assertFalse(R._worldle_area_fingerprint_changed(
            self._fp(), self._fp(), "")[0])


# ------------------------------------------- JS source guards

class TestComparisonJsGuardsIter26(unittest.TestCase):
    def test_button_click_matches_by_img_src(self):
        src = inspect.getsource(R._s_worldle_click_comparison_option)
        self.assertIn("srcCode", src)
        self.assertIn("srcMatch", src)
        self.assertIn("/' + srcCode + '.'", src)
        self.assertIn("hitKind", src)
        # Src-match requires a card-sized button with an img.
        self.assertIn("r.width >= 80 && r.height >= 80", src)

    def test_card_click_cannot_match_container_or_label_bar(self):
        src = inspect.getsource(R._s_worldle_click_comparison_card)
        # No interactive descendant (container holds the buttons).
        self.assertIn(
            "'button, a, [role=button], input, select'", src)
        # Exactly one img (container holds two; label bar none).
        self.assertIn("querySelectorAll('img').length === 1", src)
        # Prompt text holders are never cards.
        self.assertIn("which country|surface area", src)

    def test_button_click_div_fallback_excludes_label_bar(self):
        src = inspect.getsource(R._s_worldle_click_comparison_option)
        # The 26px label bar is below the 60px div-fallback floor.
        self.assertIn("r.height >= 60", src)

    def test_probe_merges_label_pill_into_generic_button(self):
        src = inspect.getsource(R._s_worldle_comparison_probe)
        self.assertIn("genericNames.has(norm(o.name))", src)

    def test_fingerprint_probe_reads_class_border_ring(self):
        src = inspect.getsource(R._s_worldle_area_button_fingerprint)
        self.assertIn("worldle-area-button-fingerprint", src)
        self.assertIn("boxShadow", src)
        self.assertIn("borderColor", src)
        self.assertIn("backgroundColor", src)

    def test_container_centre_click_is_gone(self):
        # No comparison click path clicks a container at its
        # centre: the card matcher no longer accepts a holder
        # div, and the button matcher clicks the BUTTON itself.
        for fn in (R._s_worldle_click_comparison_card,
                   R._s_worldle_click_comparison_option):
            src = inspect.getsource(fn)
            self.assertNotIn("container-centre", src)
            self.assertNotIn("centre of container", src)


# ------------------------------------------- fake-page harness

class _FakeCompPage26:
    """Fake for _s_worldle_play_comparison (Area / GDP), iter-26."""

    def __init__(self, selected="selected", card_ok=True,
                 button_ok=True, fp_change=False,
                 probe_options=None):
        self.selected = selected  # selected | none | empty
        self.card_ok = card_ok
        self.button_ok = button_ok
        self.fp_change = fp_change
        self.probe_options = (probe_options if probe_options is not None
                              else [dict(_BTN_KUWAIT),
                                    dict(_LABEL_KUWAIT),
                                    dict(_BTN_QATAR),
                                    dict(_LABEL_QATAR)])
        self.calls = []
        self.fp_calls = 0

    def _fingerprint(self):
        self.fp_calls += 1
        changed = self.fp_change and self.fp_calls >= 2
        return {"buttons": [
            {"code": "kw", "name": "Image Quiz Item",
             "cls": "card ring-2" if changed else "card",
             "border": "rgb(0, 0, 0)",
             "shadow": ("0 0 0 2px rgb(59, 130, 246)"
                        if changed else "none"),
             "bg": "rgb(255, 255, 255)", "aria_selected": "",
             "aria_pressed": "", "aria_checked": "",
             "data_selected": ""},
            {"code": "qa", "name": "Image Quiz Item", "cls": "card",
             "border": "rgb(0, 0, 0)", "shadow": "none",
             "bg": "rgb(255, 255, 255)", "aria_selected": "",
             "aria_pressed": "", "aria_checked": "",
             "data_selected": ""}],
            "submit": []}

    def evaluate(self, js, arg=None):
        if "worldle-area-button-fingerprint" in js:
            return self._fingerprint()
        if "worldle-visible-area" in js:
            return {"visible": True, "signals": ["area-prompt-visible"],
                    "prompt_visible": True, "selected_tab": "area",
                    "prompt_text": "which country has a larger surface area?",
                    "next_round_visible": False, "probe_ran": True,
                    "container": {"tag": "DIV", "cls": "widget",
                                  "rect": {"x": 400, "y": 150,
                                           "w": 800, "h": 600}},
                    "reason": "", "rejected": 0,
                    "rejection_reasons": {}}
        if "worldle-comparison-card-click" in js:
            self.calls.append("card-click")
            if self.card_ok:
                # The iter-25 failure shape would return the
                # container DIV here; the fixed card matcher
                # never returns a container, so the fake models
                # a genuine card hit.
                return [True, "Kuwait", "card", "DIV", "card-cls",
                        {"x": 709, "y": 176, "w": 248, "h": 248},
                        833, 300]
            return [False, "no card inside verified Area container",
                    "card", "", "", None, None, None]
        if "worldle-comparison-click" in js:
            self.calls.append("button-click")
            if self.button_ok:
                # The real silhouette BUTTON, matched by src
                # (9th element = match kind).
                return [True, "Image Quiz Item", "button", "BUTTON",
                        "btn-cls",
                        {"x": 709, "y": 176, "w": 248, "h": 248},
                        833, 300, "src"]
            return [False, "not found", "button", "", "", None, None,
                    None]
        if "worldle-comparison-selected-state" in js:
            self.calls.append("selected-state")
            if self.selected == "empty":
                return {}
            any_sel = self.selected == "selected"
            return {"any_selected": any_sel,
                    "selected_name": "Kuwait" if any_sel else "",
                    "selected_count": 1 if any_sel else 0,
                    "selected": [{"name": "Kuwait", "tag": "BUTTON",
                                  "cls": "selected",
                                  "signals": ["class:selected"]}]
                    if any_sel else [],
                    "options": [
                        {"name": "Kuwait", "tag": "BUTTON", "cls": "",
                         "selected": any_sel,
                         "signals": ["class:selected"] if any_sel else []},
                        {"name": "Qatar", "tag": "BUTTON", "cls": "",
                         "selected": False, "signals": []}],
                    "wanted": "Kuwait", "wanted_selected": any_sel}
        if "worldle-comparison-probe" in js:
            return {"prompt_found": True,
                    "options": [dict(o) for o in self.probe_options]}
        if "role=menuitem" in js:
            self.calls.append("loose-click")
            return [False, "not found"]
        if "localStorage.getItem" in js:
            if arg == "game_round":
                return {"gameMode": "area"}
            if arg == "size-guesses":
                return {"2026-10-18": []}
            return {}
        if "aria-selected" in js:
            return "2. AREA"
        if "document.body.innerText" in js:
            return "Which country has a larger surface area? Kuwait Qatar"
        return True

    def wait_for_timeout(self, ms):
        return None


def _run_comparison(page, mode="area", prompt_substrs=("larger surface area",),
                    lookup=None, stat="size"):
    logs = []
    submits = []
    orig_dbg = R._dbg
    orig_submit = R._s_worldle_submit_guarded
    orig_settle = R._settle
    R._dbg = _dbg_recorder(logs)
    R._s_worldle_submit_guarded = lambda *a, **k: submits.append(1) or "submitted"
    R._settle = lambda page, base=0: None
    try:
        got = R._s_worldle_play_comparison(
            page, "worldle", {}, mode, "2026-10-18", prompt_substrs,
            lookup if lookup is not None else {"kuwait": 17818,
                                               "qatar": 11000},
            stat, mode, verify_store=False)
    finally:
        R._dbg = orig_dbg
        R._s_worldle_submit_guarded = orig_submit
        R._settle = orig_settle
    return got, logs, submits


# ------------------------------------------- (a) dedup + src button

class TestAreaDedupAndSrcButtonIter26(unittest.TestCase):
    def test_four_probe_entries_deduped_to_two_and_pick_is_kuwait(self):
        page = _FakeCompPage26(selected="selected")
        (st, pick, det), logs, submits = _run_comparison(page)
        self.assertEqual(st, "attempted")
        self.assertEqual(pick, "Kuwait")
        self.assertTrue(any("deduped options 4 -> 2" in l
                            for l in logs))

    def test_button_click_records_src_match(self):
        # Card attempt fails -> button fallback clicks the real
        # silhouette BUTTON, matched by img src (match=src).
        page = _FakeCompPage26(selected="selected", card_ok=False)
        (st, pick, det), logs, submits = _run_comparison(page)
        self.assertEqual(st, "attempted")
        self.assertEqual(pick, "Kuwait")
        self.assertTrue(any("match=src" in l for l in logs),
                        logs)


# ------------------------------------------- (b) fingerprint gate

class TestAreaFingerprintGateIter26(unittest.TestCase):
    def test_fingerprint_change_proves_selection_and_submits(self):
        # The iter-25 shape: probe sees NO selection (signals
        # blind), but the button fingerprint changed -> real
        # selection signal -> submit, no gate block.
        page = _FakeCompPage26(selected="none", card_ok=False,
                               fp_change=True)
        (st, pick, det), logs, submits = _run_comparison(page)
        self.assertEqual(st, "attempted")
        self.assertEqual(pick, "Kuwait")
        self.assertEqual(len(submits), 1)
        self.assertTrue(any("selection already proven after first click"
                            in l for l in logs))

    def test_fingerprint_change_skips_retry_click(self):
        # A second click could toggle the selection off: once
        # the fingerprint proves selection after the first
        # click, no retry click is dispatched.
        page = _FakeCompPage26(selected="none", card_ok=True,
                               fp_change=True)
        (st, pick, det), logs, submits = _run_comparison(page)
        self.assertEqual(st, "attempted")
        self.assertEqual(page.calls.count("card-click"), 1)
        self.assertNotIn("button-click", page.calls)
        self.assertTrue(any("skipping retry click" in l
                            for l in logs))

    def test_no_fingerprint_change_and_no_selection_still_gates(self):
        # Gate NOT weakened: no probe selection + no fingerprint
        # diff -> partial, no submit.
        page = _FakeCompPage26(selected="none", fp_change=False)
        (st, pick, det), logs, submits = _run_comparison(page)
        self.assertEqual(st, "partial")
        self.assertIsNone(pick)
        self.assertIn("NOT submitted", det)
        self.assertIn("fingerprint: no fingerprint change", det)
        self.assertEqual(submits, [])
        self.assertIn("card-click", page.calls)
        self.assertIn("button-click", page.calls)

    def test_gdp_never_takes_a_fingerprint_and_submits_anyway(self):
        # No gate leak into GDP: buttons-first, submit-anyway,
        # and the Area fingerprint probe never runs.
        page = _FakeCompPage26(selected="none")
        (st, pick, det), logs, submits = _run_comparison(
            page, mode="gdp",
            prompt_substrs=("highest", "per capita gdp"),
            lookup={"kuwait": 80000, "qatar": 71000},
            stat="gdpPerCapita")
        self.assertEqual(st, "attempted")
        self.assertEqual(pick, "Kuwait")
        self.assertEqual(page.calls[0], "button-click")
        self.assertEqual(len(submits), 1)
        self.assertEqual(page.fp_calls, 0)


if __name__ == "__main__":
    unittest.main()
