"""Install.command, run for real in a sandbox where it can reach nothing but stub programs.

The sandbox is hermetic: PATH holds only a folder of stubs (brew, python3.12, ollama, curl, sudo, sw_vers) and a folder of symlinks to
the few ordinary tools the script uses (date, grep, sleep, ...). The Homebrew prefix is an empty temp folder, so the real Homebrew,
Python and Ollama on this Mac can never be found, let alone changed. The installer is never run for real by these tests."""

import os
import shutil
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "Install.command"
MODEL = "strands-decider:2b-mxfp8"
TOOLS = ["date", "mkdir", "grep", "head", "wc", "tr", "seq", "sleep", "kill", "cat", "dirname", "basename", "rm", "chmod", "uname", "sed", "touch",
         "cp", "env", "sh", "bash", "ls"]

BREW = """#!/bin/bash
echo "brew $*" >> "$STUB_STATE/calls.log"
case "$1" in
  shellenv) exit 0 ;;
  --version) echo "Homebrew 9.9.9"; exit 0 ;;
  install)
    case "$2" in
      python@3.12) cp "$STUB_SRC/python3.12" "$STUB_BIN/python3.12"; chmod +x "$STUB_BIN/python3.12"; exit "${STUB_BREW_PY_RC:-0}" ;;
      ollama) cp "$STUB_SRC/ollama" "$STUB_BIN/ollama"; chmod +x "$STUB_BIN/ollama"; exit 0 ;;
    esac ;;
esac
echo "brew $*" >> "$STUB_STATE/unexpected.log"; exit 2
"""

PYTHON312 = """#!/bin/bash
echo "python3.12 $*" >> "$STUB_STATE/calls.log"
if [ "$1" = "--version" ]; then echo "Python 3.12.99"; exit 0; fi
if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then mkdir -p "$3/bin"; cp "$STUB_SRC/venv_python" "$3/bin/python"; chmod +x "$3/bin/python"; exit 0; fi
echo "python3.12 $*" >> "$STUB_STATE/unexpected.log"; exit 2
"""

VENV_PYTHON = """#!/bin/bash
if [ "$1" = "--version" ]; then echo "Python 3.12.99"; exit 0; fi
if [ "$1" = "-" ]; then exec "$REAL_PY" "$@"; fi
if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then
  echo "pip $*" >> "$STUB_STATE/calls.log"
  while [ $# -gt 0 ]; do if [ "$1" = "-r" ]; then cat "$2" >> "$STUB_STATE/requirements.seen"; fi; shift; done
  exit "${STUB_PIP_RC:-0}"
fi
echo "venv python $*" >> "$STUB_STATE/unexpected.log"; exit 2
"""

OLLAMA = """#!/bin/bash
echo "ollama $*" >> "$STUB_STATE/calls.log"
case "$1" in
  serve)
    touch "$STUB_STATE/serve.up"
    trap 'rm -f "$STUB_STATE/serve.up"; echo "serve stopped" >> "$STUB_STATE/calls.log"; exit 0' TERM
    while true; do sleep 0.05; done ;;
  list) echo "NAME ID SIZE MODIFIED"; if [ -f "$STUB_STATE/model.present" ]; then echo "strands-decider:2b-mxfp8 abc 2.0GB now"; fi; exit 0 ;;
  pull)
    echo "pulling manifest"; echo "pulling abc123... 100%"
    if [ "${STUB_PULL_RC:-0}" = 0 ]; then touch "$STUB_STATE/model.present"; fi
    exit "${STUB_PULL_RC:-0}" ;;
esac
echo "ollama $*" >> "$STUB_STATE/unexpected.log"; exit 2
"""

CURL = """#!/bin/bash
echo "curl $*" >> "$STUB_STATE/calls.log"
if [ -f "$STUB_STATE/serve.up" ] || [ -n "${STUB_ALREADY_UP:-}" ]; then exit 0; fi
exit 22
"""

SUDO = """#!/bin/bash
echo "sudo $*" >> "$STUB_STATE/sudo.log"
exit 1
"""

SW_VERS = """#!/bin/bash
echo "${STUB_MACOS:-15.1}"
"""


def write_exec(path, text):
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)


class Machine:
    """A sandboxed 'Mac'. State (the project folder, installed stubs, the model) persists across run() calls, like a real computer."""

    def __init__(self, tmp_path, *, brew=True, python=False, ollama=False, sw_vers=False):
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.root = tmp_path
        self.project, self.bin, self.src, self.state, self.tools = (tmp_path / n for n in ("project", "bin", "src", "state", "tools"))
        for d in (self.project, self.bin, self.src, self.state, self.tools):
            d.mkdir()
        shutil.copy(SCRIPT, self.project / "Install.command")
        shutil.copy(ROOT / "pyproject.toml", self.project / "pyproject.toml")
        for name in TOOLS:
            found = shutil.which(name)
            if found:
                (self.tools / name).symlink_to(found)
        write_exec(self.src / "python3.12", PYTHON312)
        write_exec(self.src / "venv_python", VENV_PYTHON)
        write_exec(self.src / "ollama", OLLAMA)
        write_exec(self.bin / "curl", CURL)
        write_exec(self.bin / "sudo", SUDO)
        if brew:
            write_exec(self.bin / "brew", BREW)
        if python:
            shutil.copy(self.src / "python3.12", self.bin / "python3.12")
        if ollama:
            shutil.copy(self.src / "ollama", self.bin / "ollama")
        if sw_vers:
            write_exec(self.bin / "sw_vers", SW_VERS)

    def run(self, **extra_env):
        env = {"PATH": f"{self.bin}{os.pathsep}{self.tools}", "HOME": str(self.root / "home"), "HEADSUP_BREW_PREFIX": str(self.root / "no-homebrew-here"),
               "HEADSUP_UNAME_S": "Darwin", "HEADSUP_UNAME_M": "arm64", "STUB_STATE": str(self.state), "STUB_SRC": str(self.src),
               "STUB_BIN": str(self.bin), "REAL_PY": sys.executable}
        env.update({k: str(v) for k, v in extra_env.items()})
        return subprocess.run(["/bin/bash", str(self.project / "Install.command")], cwd=self.root, env=env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=60)

    def calls(self):
        path = self.state / "calls.log"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def log(self):
        path = self.project / "data" / "install.log"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def count(self, prefix):
        return len([c for c in self.calls() if c.startswith(prefix)])

    def unexpected(self):
        path = self.state / "unexpected.log"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def pins():
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]


# ------------------------------------------------------------------------------------------------------ the script itself
def test_the_installer_is_an_executable_bash_script_that_cannot_ask_for_rights_or_run_a_download_pipe():
    text = SCRIPT.read_text(encoding="utf-8")
    assert os.access(SCRIPT, os.X_OK) and text.startswith("#!/bin/bash")
    assert "sudo" not in text and ".env" not in text and "rm -rf" not in text
    assert "raw.githubusercontent.com" not in text and "Homebrew/install" not in text, "it never runs Homebrew's own installer"
    import re
    assert not re.search(r"\|\s*(ba|z)?sh\b", text), "nothing is piped into a shell"
    assert "set -x" not in text and "printenv" not in text and "declare -p" not in text and "export -p" not in text and not re.search(r"^\s*env\s*$", text, re.M)
    assert "https://brew.sh" in text and "data/install.log" in text and "pyproject.toml" in text and "trap stop_serve EXIT" in text


def test_the_script_reads_its_package_list_from_pyproject_so_it_cannot_drift_from_the_pins():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "tomllib" in text and 'project["dependencies"]' in text
    assert not any(pin in text for pin in pins()), "no version number is written into the script"
    assert all("==" in pin for pin in pins()), "and every pin is exact"


# ------------------------------------------------------------------------------------------------------ refusals
def test_without_homebrew_it_explains_gives_the_link_and_installs_nothing(tmp_path):
    m = Machine(tmp_path, brew=False)
    done = m.run()
    assert done.returncode == 1
    assert "https://brew.sh" in done.stdout and "asks for your Mac password" in done.stdout and "double-click Install.command again" in done.stdout
    assert m.calls() == [] and not (m.project / ".venv").exists() and not (m.state / "sudo.log").exists()
    assert "STOPPED at: Homebrew" in m.log() and "exit 1 (Homebrew)" in m.log()


def test_an_intel_mac_or_a_non_mac_is_told_why_and_nothing_runs(tmp_path):
    m = Machine(tmp_path)
    done = m.run(HEADSUP_UNAME_M="x86_64")
    assert done.returncode == 1 and "Apple silicon" in done.stdout and "x86_64" in done.stdout and m.calls() == []
    done = m.run(HEADSUP_UNAME_S="Linux")
    assert done.returncode == 1 and "built for a Mac" in done.stdout and m.calls() == []


def test_an_older_macos_is_warned_about_but_not_blocked(tmp_path):
    old = Machine(tmp_path / "old", python=True, ollama=True, sw_vers=True)
    done = old.run(STUB_MACOS="14.5")
    assert done.returncode == 0 and "Homebrew officially supports macOS 15" in done.stdout and "14.5" in done.stdout
    current = Machine(tmp_path / "current", python=True, ollama=True, sw_vers=True)
    done = current.run(STUB_MACOS="15.1")
    assert done.returncode == 0 and "officially supports" not in done.stdout and "macOS 15.1" in current.log()


def test_a_venv_folder_with_the_wrong_python_is_left_alone_and_explained(tmp_path):
    m = Machine(tmp_path, python=True, ollama=True)
    old = m.project / ".venv" / "bin"
    old.mkdir(parents=True)
    write_exec(old / "python", '#!/bin/bash\n[ "$1" = "--version" ] && echo "Python 3.11.4"\n')
    before = (old / "python").read_text()
    done = m.run()
    assert done.returncode == 1 and "does not use Python 3.12" in done.stdout and "Nothing was changed" in done.stdout
    assert (old / "python").read_text() == before, "it never deletes or rewrites the folder"
    assert m.count("pip") == 0 and m.count("ollama") == 0


# ------------------------------------------------------------------------------------------------------ a clean machine
def test_a_fresh_mac_gets_python_ollama_the_venv_the_pinned_packages_and_the_model_in_that_order(tmp_path):
    m = Machine(tmp_path)  # Homebrew only
    done = m.run()
    assert done.returncode == 0, done.stdout + done.stderr
    calls = m.calls()

    def at(prefix):
        return next(i for i, c in enumerate(calls) if c.startswith(prefix))

    order = [at("brew install python@3.12"), at("brew install ollama"), at("python3.12 -m venv .venv"), at("pip -m pip install"), at("ollama serve"),
             at("ollama list"), at("ollama pull strands-decider:2b-mxfp8"), at("serve stopped")]
    assert order == sorted(order) and len(set(order)) == len(order)
    assert m.count("brew install") == 2 and m.unexpected() == [] and not (m.state / "sudo.log").exists()
    assert (m.project / ".venv" / "bin" / "python").exists() and not (m.state / "serve.up").exists(), "the Ollama it started is stopped again"
    assert "All done. Double-click Heads-Up.command" in done.stdout and "setup page" in done.stdout


def test_exactly_the_pins_in_pyproject_are_installed(tmp_path):
    m = Machine(tmp_path)
    m.run()
    seen = (m.state / "requirements.seen").read_text(encoding="utf-8").split()
    assert seen == pins() and len(seen) >= 5
    assert any(c.startswith("pip -m pip install --disable-pip-version-check -r ") for c in m.calls())


def test_the_model_is_pulled_with_a_model_folder_inside_the_project_and_its_progress_is_shown_not_logged(tmp_path):
    m = Machine(tmp_path)
    done = m.run()
    assert "pulling manifest" in done.stdout and "pulling abc123... 100%" in done.stdout, "the progress goes straight to the window"
    assert "pulling" not in m.log(), "the log keeps step names and exit codes only"
    assert "model download started" in m.log() and "model download finished with exit code 0" in m.log()
    assert (m.project / ".cache" / "ollama").is_dir()


# ------------------------------------------------------------------------------------------------------ running it twice
def test_a_second_run_installs_and_downloads_nothing(tmp_path):
    m = Machine(tmp_path)
    assert m.run().returncode == 0
    first = len(m.calls())
    second = m.run()
    assert second.returncode == 0
    new = m.calls()[first:]
    assert not any(c.startswith("brew install") for c in new), new
    assert not any(c.startswith("ollama pull") for c in new) and not any(c.startswith("python3.12 -m venv") for c in new)
    assert second.stdout.count("Already") >= 4 and "Already downloaded" in second.stdout and "Already there (Python 3.12)" in second.stdout
    assert m.log().count("Install started") == 2 and m.log().count("Install finished OK") == 2, "the log is appended to, never replaced"
    assert m.unexpected() == []


def test_when_python_and_ollama_are_already_installed_homebrew_is_not_asked_to_install_them(tmp_path):
    m = Machine(tmp_path, python=True, ollama=True)
    assert m.run().returncode == 0
    assert m.count("brew install") == 0


def test_an_ollama_that_is_already_running_is_used_and_left_running(tmp_path):
    m = Machine(tmp_path, python=True, ollama=True)
    done = m.run(STUB_ALREADY_UP="1")
    assert done.returncode == 0 and "Ollama is already running on this Mac; using it." in done.stdout
    assert m.count("ollama serve") == 0 and "serve stopped" not in " ".join(m.calls()), "it did not start it, so it does not stop it"


# ------------------------------------------------------------------------------------------------------ failures
def test_a_failed_download_stops_names_the_step_and_still_stops_the_ollama_it_started(tmp_path):
    m = Machine(tmp_path, python=True, ollama=True)
    done = m.run(STUB_PULL_RC="1")
    assert done.returncode == 1 and "STOPPED at: downloading the model" in done.stdout and "carries on where it stopped" in done.stdout
    assert "serve stopped" in m.calls() and not (m.state / "serve.up").exists()
    assert "model download finished with exit code 1" in m.log() and "exit 1 (downloading the model)" in m.log()
    assert "Install finished OK" not in m.log()
    ok = m.run(STUB_PULL_RC="0")
    assert ok.returncode == 0 and "Install finished OK" in m.log() and "model download finished with exit code 0" in m.log(), "running it again completes the job"


def test_a_failed_package_install_names_the_step(tmp_path):
    m = Machine(tmp_path, python=True, ollama=True)
    done = m.run(STUB_PIP_RC="1")
    assert done.returncode == 1 and "STOPPED at: installing the packages" in done.stdout and "internet connection" in done.stdout
    assert m.count("ollama") == 0, "nothing after the failed step ran"


def test_homebrew_failing_to_install_python_stops_with_a_message_not_a_traceback(tmp_path):
    m = Machine(tmp_path)
    done = m.run(STUB_BREW_PY_RC="1")
    assert done.returncode == 1 and "STOPPED at: installing Python 3.12" in done.stdout and "Traceback" not in done.stdout + done.stderr


# ------------------------------------------------------------------------------------------------------ privacy
def test_no_secret_in_the_environment_reaches_the_screen_or_the_log(tmp_path):
    names = ["ANTHROPIC_API_KEY", "GITHUB_TOKEN", "TELEGRAM_BOT_TOKEN", "ICAL_URL"]
    values = ["fake-anthropic-" + "key-for-install-test", "fake-github-" + "token-for-install-test",
              "123456:" + "FAKE-telegram-token-for-install-test", "https://calendar.example.invalid/" + "private-for-install-test/a.ics"]
    fake = dict(zip(names, values))
    m = Machine(tmp_path)
    done = m.run(**fake)
    everything = done.stdout + done.stderr + m.log() + (m.project / "data" / "install-requirements.txt").read_text() + "\n".join(m.calls())
    everything += (m.project / "data" / "ollama-serve.log").read_text() if (m.project / "data" / "ollama-serve.log").exists() else ""
    assert done.returncode == 0
    for value in fake.values():
        assert value not in everything
    assert "private-for-install-test" not in everything


def test_the_log_holds_step_names_versions_and_exit_codes_and_the_installer_never_touches_the_settings_or_the_stubs_unexpectedly(tmp_path):
    m = Machine(tmp_path)
    m.run()
    log = m.log()
    for needle in ("Install started", "STEP Homebrew", "brew: Homebrew 9.9.9", "python: Python 3.12.99", "packages:", "Install finished OK"):
        assert needle in log, needle
    assert all(line[:4].isdigit() for line in log.splitlines()), "every line is time-stamped"
    assert not (m.project / ".env").exists(), "the installer never creates or reads the settings file"
    assert m.unexpected() == []
