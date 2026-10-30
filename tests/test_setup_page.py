"""The /setup page's own JavaScript, run for real in Node against a strict fake DOM (tests/js/setup_page_harness.js). The fake DOM throws
on innerHTML and on anything that is not a node or a string, so a page that built HTML from server text, or passed an array where
a real browser would print "[object ...]", fails here. Skipped when Node is not installed; the checks of the page's HTML, its CSP and
its server side are in test_setup_server.py and run everywhere."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from headsup.ui_setup_page import render_setup_page

HARNESS = Path(__file__).parent / "js" / "setup_page_harness.js"
NODE = shutil.which("node")


def script_of(html):
    return re.search(r'<script nonce="[^"]+">(.*)</script>', html, re.S).group(1)


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    if NODE is None:
        pytest.skip("node is not installed")
    path = tmp_path_factory.mktemp("page") / "setup_page.js"
    path.write_text(script_of(render_setup_page("tok-for-tests", "nonce-for-tests")), encoding="utf-8")
    done = subprocess.run([NODE, str(HARNESS), str(path)], capture_output=True, text=True, timeout=60,
                          env={**os.environ, "TZ": "Asia/Kolkata"})
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout)


def test_the_page_script_is_valid_javascript_and_runs_every_scenario(report):
    assert len(report) >= 25


def test_every_scenario_of_the_page_passes(report):
    failed = [f"{r['name']}: {r.get('error')}" for r in report if not r["ok"]]
    assert failed == []


def test_the_scenarios_cover_the_promises_that_matter(report):
    names = " | ".join(r["name"] for r in report)
    for promise in ("password box", "plain text", "empties every key box", "nothing is saved until the click", "Apply", "a key typed on the page",
                    "session token", "double click", "not tested", "keeps what the user typed"):
        assert promise.lower() in names.lower(), promise


def test_the_script_has_no_inline_handler_no_html_assignment_and_no_url():
    script = script_of(render_setup_page("t", "n"))
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "http://", "https://", "XMLHttpRequest", "localStorage", "sessionStorage"):
        assert banned not in script, banned
    assert script.count("fetch(") == 1, "one place sends requests"


def test_the_outcome_bar_comes_before_the_cards_in_the_real_page():
    html = render_setup_page("t", "n")
    assert html.index('id="message"') < html.index('id="app"'), "an outcome written below six cards is off screen"
    assert "#message { position:sticky; top:0" in html
