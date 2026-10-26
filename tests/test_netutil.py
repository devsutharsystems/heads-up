import httpx
import pytest

from headsup import netutil
from headsup.haiku import build_client
from headsup.sources.github import GitHubSource
from headsup.sources.ical import IcalError, IcalSource
from headsup.store import Store
from datetime import datetime, timezone
from pathlib import Path
import inspect

SRC = Path(__file__).resolve().parent.parent / "src" / "headsup"
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
ICS = (Path(__file__).parent / "fixtures" / "ical" / "moved_before.ics").read_bytes()
URL = "https://calendar.example.invalid/secret-address-made-up/basic.ics"


def test_timeout_values():
    t = netutil.TIMEOUT
    assert (t.connect, t.read, t.write, t.pool) == (10.0, 15.0, 15.0, 15.0)


def test_with_connect_retry_retries_once_and_only_for_connect_timeouts():
    calls = []

    def flaky(*failures):
        queue = list(failures)

        def send():
            calls.append(1)
            if queue:
                raise queue.pop(0)
            return "ok"
        return send

    calls.clear()
    assert netutil.with_connect_retry(flaky(httpx.ConnectTimeout("t"))) == "ok" and len(calls) == 2
    calls.clear()
    with pytest.raises(httpx.ConnectTimeout):
        netutil.with_connect_retry(flaky(httpx.ConnectTimeout("t"), httpx.ConnectTimeout("t")))
    assert len(calls) == 2  # one retry, never a third attempt
    for exc in (httpx.ReadTimeout("t"), httpx.ConnectError("t"), httpx.PoolTimeout("t"), httpx.WriteTimeout("t")):
        calls.clear()
        with pytest.raises(type(exc)):
            netutil.with_connect_retry(flaky(exc))
        assert len(calls) == 1  # other errors are not retried


# ---- GitHub source

def github_source(tmp_path, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GitHubSource(["example-org/demo-repo"], client, Store(tmp_path / "t.db"), now=lambda: NOW)


def test_github_source_retries_one_connect_timeout_then_succeeds(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        if len(seen) == 1:
            raise httpx.ConnectTimeout("slow")
        return httpx.Response(200, json=[])

    github_source(tmp_path, handler).poll()
    assert seen[0].url == seen[1].url and seen[0].url.path.endswith("/releases")  # the same request, once more
    assert all(r.extensions["timeout"] == {"connect": 10.0, "read": 15.0, "write": 15.0, "pool": 15.0} for r in seen)


def test_github_source_gives_up_after_two_connect_timeouts(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        raise httpx.ConnectTimeout("slow")

    with pytest.raises(httpx.ConnectTimeout):
        github_source(tmp_path, handler).poll()
    assert len(seen) == 2


@pytest.mark.parametrize("exc", [httpx.ReadTimeout("slow"), httpx.ConnectError("refused")])
def test_github_source_does_not_retry_other_errors(tmp_path, exc):
    seen = []

    def handler(request):
        seen.append(request)
        raise exc

    with pytest.raises(type(exc)):
        github_source(tmp_path, handler).poll()
    assert len(seen) == 1


# ---- iCal source

def ical_source(tmp_path, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return IcalSource(URL, client, Store(tmp_path / "t.db"), now=lambda: NOW)


def test_ical_source_timeouts_and_one_connect_retry(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        if len(seen) == 1:
            raise httpx.ConnectTimeout("slow")
        return httpx.Response(200, content=ICS)

    ical_source(tmp_path, handler).poll()
    assert len(seen) == 2 and all(r.extensions["timeout"] == {"connect": 10.0, "read": 15.0, "write": 15.0, "pool": 15.0} for r in seen)


def test_ical_source_gives_up_after_two_connect_timeouts_and_never_retries_other_errors(tmp_path):
    seen = []

    def always(exc):
        def handler(request):
            seen.append(request)
            raise exc
        return handler

    with pytest.raises(httpx.ConnectTimeout):
        ical_source(tmp_path, always(httpx.ConnectTimeout("slow"))).poll()
    assert len(seen) == 2
    seen.clear()
    with pytest.raises(httpx.ReadTimeout):
        ical_source(tmp_path, always(httpx.ReadTimeout("slow"))).poll()
    assert len(seen) == 1


def test_a_bad_status_is_not_retried(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(500)

    with pytest.raises(IcalError):
        ical_source(tmp_path, handler).poll()
    assert len(seen) == 1


# ---- Haiku keeps its no-retry rule

def test_haiku_client_still_has_no_retries_and_the_helper_is_not_used_for_it():
    assert build_client("fake-key-for-test").max_retries == 0
    users = sorted(p.name for p in SRC.rglob("*.py") if "with_connect_retry" in p.read_text() and p.name != "netutil.py")
    assert users == ["github.py", "ical.py"]  # not the writer, the decider, Telegram or the rerun POST
