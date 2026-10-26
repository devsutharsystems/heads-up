"""Timeouts and the single connect retry shared by the GitHub and iCal sources.

Connect 10 s; read, write and pool 15 s. One retry, and only when the CONNECT timed out (the request never
reached the server, so repeating a GET is safe). Read timeouts, other errors and the Haiku client (which keeps
its no-retry rule) are not retried.
"""

import httpx

TIMEOUT = httpx.Timeout(15.0, connect=10.0)


def with_connect_retry(send):
    """Run send(); if it raises ConnectTimeout, run it once more and let a second failure propagate."""
    try:
        return send()
    except httpx.ConnectTimeout:
        return send()
