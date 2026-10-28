"""Start and stop `ollama serve` for the dashboard (--ui only), and check that the Decider model is installed.

If something already answers on the Decider host, nothing is started and nothing is stopped. Only 127.0.0.1 or
localhost is ever started (Decider.check_local_host). The model is never pulled automatically (about 2.0 GB):
the page shows the pull command instead.
"""

import os
import shutil
import subprocess
import time
from pathlib import Path

import httpx

from headsup.config import ollama_serve_env
from headsup.decider import DECIDER_MODEL, check_local_host

OLLAMA_BIN = "/opt/homebrew/bin/ollama"
ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = ROOT / ".cache" / "ollama"
LOG_PATH = ROOT / "data" / "ollama-serve.log"


def pull_command(host):
    return (f'cd "<project folder>" && OLLAMA_HOST={host} OLLAMA_MODELS="$PWD/.cache/ollama" '
            f"{OLLAMA_BIN} pull {DECIDER_MODEL}")


class OllamaChild:
    def __init__(self, client, host, *, models_dir=MODELS_DIR, log_path=LOG_PATH, popen=subprocess.Popen,
                 which=shutil.which, exists=os.path.exists, sleep=None, monotonic=None, wait_s=15.0):
        self.client, self.host = client, host
        self.models_dir, self.log_path = Path(models_dir), Path(log_path)
        self._popen, self._which, self._exists = popen, which, exists
        self._sleep, self._monotonic, self.wait_s = sleep or time.sleep, monotonic or time.monotonic, wait_s
        self.process = None
        self.started_by_us = False

    def is_up(self):
        """Any HTTP answer within 2 s counts as up (the same rule as pipeline.decider_is_up)."""
        try:
            self.client.get(f"http://{self.host}/", timeout=2.0)
            return True
        except httpx.HTTPError:
            return False

    def _binary(self):
        if self._exists(OLLAMA_BIN):
            return OLLAMA_BIN
        return self._which("ollama")

    def ensure_running(self):
        """'already running', 'started', or a short reason it could not be started."""
        try:
            check_local_host(self.host)
        except ValueError:
            return "refused: the Decider host must be 127.0.0.1 or localhost"
        if self.is_up():
            return "already running"
        binary = self._binary()
        if not binary:
            return "not started: ollama was not found"
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "ab") as log:
            try:
                self.process = self._popen([binary, "serve"], env=ollama_serve_env(self.host, self.models_dir),
                                           stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            except OSError as exc:
                return f"not started: {type(exc).__name__}"
        self.started_by_us = True
        deadline = self._monotonic() + self.wait_s
        while self._monotonic() < deadline:
            if self.is_up():
                return "started"
            if self.process.poll() is not None:
                return "not started: ollama exited"
            self._sleep(0.5)
        return "not started: no answer yet"

    def models(self):
        """Installed model names (GET /api/tags, field `name`: https://docs.ollama.com/api/tags), or None if unknown."""
        try:
            resp = self.client.get(f"http://{self.host}/api/tags", timeout=2.0)
            if resp.status_code != 200:
                return None
            return [m.get("name") or m.get("model") for m in resp.json().get("models", [])]
        except (httpx.HTTPError, ValueError, AttributeError):
            return None

    def model_status(self):
        names = self.models()
        if names is None:
            return "unknown"
        return "present" if DECIDER_MODEL in names else "missing"

    def health(self):
        up = self.is_up()
        return {"up": up, "started_by_us": self.started_by_us, "model": self.model_status() if up else "unknown",
                "model_name": DECIDER_MODEL, "pull_command": pull_command(self.host)}

    def stop(self):
        """Stop the server only if we started it."""
        if not self.started_by_us or self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.started_by_us = False
