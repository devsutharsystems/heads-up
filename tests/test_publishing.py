"""Publishing files: the CI workflow, the GitHub Pages page and the README badge."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = (ROOT / ".github" / "workflows" / "tests.yml").read_text()
PAGE = (ROOT / "docs" / "index.html").read_text()
README = (ROOT / "README.md").read_text()
REPO_URL = "https://github.com/devsutharsystems/heads-up"
PAGES_URL = "https://devsutharsystems.github.io/heads-up/"
HEADLINE = ("the Decider scored higher than the rules on precision, recall and F1, but every gap is within the uncertainty. "
            "It is not proven better.")


# ---------------------------------------------------------------- the workflow (text checks; no YAML library installed)

def test_workflow_runs_only_for_main_pushes_and_pull_requests_to_main():
    triggers = WORKFLOW.split("\njobs:")[0]
    assert re.search(r"^on:\n  push:\n    branches: \[main\]\n  pull_request:\n    branches: \[main\]\n", triggers, re.M)
    assert "pull_request_target" not in WORKFLOW and "workflow_dispatch" not in WORKFLOW and "schedule" not in WORKFLOW
    assert "starter" not in WORKFLOW.replace("starter branch fails its guiding tests on purpose", "")


def test_workflow_needs_no_secrets_and_has_a_read_only_token():
    assert "${{ secrets" not in WORKFLOW and "${{" not in WORKFLOW  # no expression at all, so no secret can be referenced
    assert re.search(r"^permissions:\n  contents: read\n", WORKFLOW, re.M)
    assert "persist-credentials: false" in WORKFLOW


def test_workflow_installs_the_pins_from_pyproject_then_tests_then_scans_for_secrets():
    assert "runs-on: ubuntu-latest" in WORKFLOW and 'python-version: "3.12"' in WORKFLOW
    install = WORKFLOW.index("pyproject.toml")
    test = WORKFLOW.index("python -m pytest")
    scan = WORKFLOW.index("detect-secrets-hook --baseline .secrets.baseline")
    assert install < test < scan
    assert 'project["dependencies"] + project["optional-dependencies"]["dev"]' in WORKFLOW


def test_the_pyproject_pins_the_workflow_installs_are_all_exact():
    import tomllib
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    pins = project["dependencies"] + project["optional-dependencies"]["dev"]
    assert len(pins) == 7 and all("==" in p for p in pins)
    assert any(p.startswith("pytest==") for p in pins) and any(p.startswith("detect-secrets==") for p in pins)


# ---------------------------------------------------------------- the Pages page

def test_page_has_no_scripts_external_assets_or_plain_http():
    low = PAGE.lower()
    for forbidden in ("<script", "<link", "@import", "http://", "<iframe", "onclick="):
        assert forbidden not in low, forbidden
    assert "url(" not in low  # no remote or local CSS assets


def test_page_is_mobile_friendly_and_light_dark_aware():
    assert PAGE.startswith("<!doctype html>") and '<html lang="en">' in PAGE
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in PAGE
    assert "prefers-color-scheme: dark" in PAGE and PAGE.count("<h1>") == 1
    assert "overflow-x: auto" in PAGE and "max-width: 100%" in PAGE
    assert re.search(r"body \{[^}]*background: var\(--bg\)", PAGE)


def test_page_images_exist_and_have_alt_text():
    images = re.findall(r'<img src="([^"]+)"[^>]*alt="([^"]+)"', PAGE)
    assert [src for src, _ in images] == ["screenshot-alert.png", "screenshot-rerun.png"]
    assert all(alt.strip() for _, alt in images)
    for src, _ in images:
        assert (ROOT / "docs" / src).is_file()
    assert (ROOT / "docs" / ".nojekyll").is_file()


def test_page_links_go_to_the_repo_and_every_target_is_there():
    links = re.findall(r'href="([^"]+)"', PAGE)
    assert links and all(l.startswith(REPO_URL) for l in links)
    for needed in ("/blob/main/README.md", "/blob/main/LEARNERS.md", "/blob/main/eval/EVAL_REPORT.md", "/tree/starter"):
        assert REPO_URL + needed in links
    for l in links:
        if "/blob/main/" in l:
            assert (ROOT / l.split("/blob/main/")[1]).is_file(), l


def test_page_has_the_demo_video_placeholder_and_the_required_sections():
    assert "Demo video: link to be added." in PAGE
    for section in ("What it is", "How it works", "Results", "First live run (27 Oct 2026)", "Screenshots", "Limits and disclosures"):
        assert f">{section}</h2>" in PAGE
    assert "Inspired by Claude Duet" in PAGE


def test_page_numbers_and_headline_match_the_readme():
    results = README.split("## 4. Results")[1].split("**Pilot (before building).**")[0]
    numbers = set(re.findall(r"\d+\.\d+", results))
    assert len(numbers) > 20
    text = re.sub(r"<[^>]+>", " ", PAGE)
    missing = sorted(n for n in numbers if n not in text)
    assert not missing, missing
    assert HEADLINE in README and HEADLINE in PAGE.replace("<strong>", "").replace("</strong>", "").replace("Headline: ", "")


def test_page_first_live_run_text_matches_the_readme():
    section = README.split("## First live run (27 Oct 2026)")[1].split("## 5.")[0]
    plain = re.sub(r"<[^>]+>", "", PAGE).replace("&gt;", ">").replace("&lt;", "<")
    for line in section.splitlines():
        line = line.strip().lstrip("- ").replace("**", "").replace("`", "")
        if line:
            assert line in plain.replace("`", ""), line


# ---------------------------------------------------------------- the README

def test_readme_has_the_ci_badge_and_the_pages_link_marked_not_live_yet():
    badge = f"[![tests]({REPO_URL}/actions/workflows/tests.yml/badge.svg?branch=main)]({REPO_URL}/actions/workflows/tests.yml)"
    assert badge in README
    assert f"<{PAGES_URL}> (**live after the repo is public**)" in README
