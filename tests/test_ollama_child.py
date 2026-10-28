import subprocess
from types import SimpleNamespace

import httpx

from headsup.decider import DECIDER_MODEL
from headsup.ollama_child import OLLAMA_BIN, OllamaChild, pull_command

HOST = "127.0.0.1:11434"


class FakeClient:
    def __init__(self, up=True, models=None):
        self.up, self.models, self.requests = up, models, []

    def get(self, url, timeout=None):
        self.requests.append(url)
        if not self.up:
            raise httpx.ConnectError("down")
        if url.endswith("/api/tags"):
            if self.models is None:
                return SimpleNamespace(status_code=500, json=lambda: {})
            return SimpleNamespace(status_code=200, json=lambda: {"models": [{"name": m, "model": m} for m in self.models]})
        return SimpleNamespace(status_code=200)


class FakeProc:
    def __init__(self):
        self.terminated = self.killed = False
        self.waited = 0

    def poll(self):
        return None if not self.terminated else 0

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        self.waited += 1

    def kill(self):
        self.killed = True


def make(client, tmp_path, exists=True, which=None, procs=None):
    procs = procs if procs is not None else []

    def popen(cmd, **kw):
        proc = FakeProc()
        procs.append((cmd, kw, proc))
        client.up = True  # the "server" answers once started
        return proc

    return OllamaChild(client, HOST, models_dir=tmp_path / "models", log_path=tmp_path / "log" / "o.log", popen=popen,
                       which=lambda n: which, exists=lambda p: exists, sleep=lambda s: None, wait_s=1.0), procs


def test_already_up_means_nothing_is_started_or_stopped(tmp_path):
    child, procs = make(FakeClient(up=True), tmp_path)
    assert child.ensure_running() == "already running" and procs == []
    child.stop()
    assert child.started_by_us is False


def test_down_starts_ollama_serve_with_the_project_settings_and_stops_it_on_exit(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-be-passed-on-0000")
    child, procs = make(FakeClient(up=False), tmp_path)
    assert child.ensure_running() == "started"
    (cmd, kw, proc), = procs
    assert cmd == [OLLAMA_BIN, "serve"]
    env = kw["env"]
    assert env["OLLAMA_HOST"] == HOST and env["OLLAMA_MODELS"] == str(tmp_path / "models") and env["OLLAMA_KEEP_ALIVE"] == "60s"
    assert "ANTHROPIC_API_KEY" not in env and not any("TOKEN" in k or "KEY" in k for k in env)
    assert child.started_by_us is True
    child.stop()
    assert proc.terminated and proc.waited == 1 and child.started_by_us is False


def test_the_plain_ollama_name_is_used_when_the_homebrew_path_is_missing(tmp_path):
    child, procs = make(FakeClient(up=False), tmp_path, exists=False, which="/usr/local/bin/ollama")
    assert child.ensure_running() == "started" and procs[0][0] == ["/usr/local/bin/ollama", "serve"]
    child2, procs2 = make(FakeClient(up=False), tmp_path, exists=False, which=None)
    assert child2.ensure_running() == "not started: ollama was not found" and procs2 == []


def test_a_non_local_host_is_refused_and_nothing_is_started(tmp_path):
    client = FakeClient(up=False)
    child, procs = make(client, tmp_path)
    child.host = "192.168.1.9:11434"
    assert child.ensure_running().startswith("refused") and procs == [] and client.requests == []


def test_model_status_present_missing_or_unknown(tmp_path):
    assert make(FakeClient(models=[DECIDER_MODEL, "other"]), tmp_path)[0].model_status() == "present"
    assert make(FakeClient(models=["other"]), tmp_path)[0].model_status() == "missing"
    assert make(FakeClient(models=None), tmp_path)[0].model_status() == "unknown"
    h = make(FakeClient(models=["other"]), tmp_path)[0].health()
    assert h["model"] == "missing" and h["pull_command"] == pull_command(HOST)
    assert DECIDER_MODEL in h["pull_command"] and "OLLAMA_MODELS" in h["pull_command"] and h["up"] is True
    down = make(FakeClient(up=False), tmp_path)[0].health()
    assert down["up"] is False and down["model"] == "unknown"


def test_a_server_that_ignores_terminate_is_killed(tmp_path):
    child, procs = make(FakeClient(up=False), tmp_path)
    child.ensure_running()
    proc = procs[0][2]
    proc.wait = lambda timeout=None: (_ for _ in ()).throw(subprocess.TimeoutExpired("ollama", 5)) if not proc.killed else None
    child.stop()
    assert proc.terminated and proc.killed
