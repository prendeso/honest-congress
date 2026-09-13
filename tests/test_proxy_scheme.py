"""Redirects must not downgrade an HTTPS request to http://.

Reported as "marco rubio showed Failed to load anomalies for this member", and
reproduced against production:

    GET /api/anomalies?member_id=12217   ->  307
    location: http://honest-congress-production.up.railway.app/api/anomalies/?...

FastAPI answers a URL missing its trailing slash with a 307 to an ABSOLUTE URL
built from the scheme it believes it is serving. Behind Railway's
TLS-terminating proxy the app believed that was plain HTTP, so the browser
blocked the redirect as mixed content on an HTTPS page, `fetch()` rejected, and
the modal reported a failure. `curl` follows the same redirect happily, which is
exactly why the endpoint looked healthy from a terminal.

The cause was NOT a missing --proxy-headers: uvicorn enables that by default.
It was `forwarded_allow_ips`, which defaults to "127.0.0.1" -- so the proxy
header is honoured only when the peer is loopback, and Railway's proxy is not.

That detail is why these tests are shaped the way they are:

  * `TestClient` cannot catch this at all. ProxyHeadersMiddleware is wrapped
    around the app by uvicorn's Config, not by the ASGI app, so a TestClient
    request never passes through it and sees the same result before and after
    the fix. The scheme test therefore runs a REAL uvicorn process.
  * The request has to come from a NON-LOOPBACK peer. Sent from 127.0.0.1 the
    default configuration trusts the header and the bug does not reproduce --
    which is the trap that makes this look fine in local testing.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path

import pytest
import requests

REPO_ROOT = Path(__file__).resolve().parents[1]


def _non_loopback_address() -> str | None:
    """An address of this host that is not 127.0.0.1, or None."""
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.0.2.1", 1))  # TEST-NET-1: routed nowhere, no packets sent
        address = probe.getsockname()[0]
        probe.close()
    except OSError:
        return None
    return None if address.startswith("127.") else address


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return int(s.getsockname()[1])


class _Server:
    def __init__(self, env_extra: dict[str, str], tmp_path: Path):
        self.port = _free_port()
        env = {
            **os.environ,
            "DATABASE_URL": f"sqlite:///{tmp_path / 'proxy_probe.db'}",
            **env_extra,
        }
        env.pop("ENV", None)
        self.proc = subprocess.Popen(
            [
                "python",
                "-m",
                "uvicorn",
                "src.api.main:app",
                "--host",
                "0.0.0.0",
                "--port",
                str(self.port),
            ],
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

    def wait_until_ready(self, address: str, timeout: float = 45.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                return False
            try:
                requests.get(f"http://{address}:{self.port}/health", timeout=2)
                return True
            except requests.RequestException:
                time.sleep(0.3)
        return False

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            self.proc.kill()


@pytest.fixture(scope="module")
def peer_address():
    address = _non_loopback_address()
    if not address:
        pytest.skip(
            "no non-loopback address on this host; the downgrade only reproduces "
            "when the peer is outside forwarded_allow_ips' 127.0.0.1 default"
        )
    return address


def _redirect_location(server: _Server, address: str) -> str:
    response = requests.get(
        f"http://{address}:{server.port}/api/anomalies?limit=1",
        headers={"X-Forwarded-Proto": "https"},
        allow_redirects=False,
        timeout=15,
    )
    assert response.status_code == 307, response.status_code
    return response.headers["location"]


class TestAProxiedHttpsRequestKeepsItsScheme:
    def test_the_redirect_is_not_downgraded(self, peer_address, tmp_path):
        """The whole bug, end to end, against a real server."""
        server = _Server({"FORWARDED_ALLOW_IPS": "*"}, tmp_path)
        try:
            assert server.wait_until_ready(peer_address), (
                f"server did not start: {server.proc.stdout.read().decode()[-2000:]}"
            )
            location = _redirect_location(server, peer_address)
        finally:
            server.stop()

        assert location.startswith("https://"), (
            f"an HTTPS request was redirected to {location!r} -- a browser blocks "
            "this as mixed content and fetch() rejects"
        )

    def test_without_the_setting_it_downgrades(self, peer_address, tmp_path):
        """The control. Without this the test above could pass for the wrong
        reason -- if the redirect never happened, or the header were honoured by
        something else, the assertion would hold while proving nothing."""
        server = _Server({"FORWARDED_ALLOW_IPS": "127.0.0.1"}, tmp_path)
        try:
            assert server.wait_until_ready(peer_address), (
                f"server did not start: {server.proc.stdout.read().decode()[-2000:]}"
            )
            location = _redirect_location(server, peer_address)
        finally:
            server.stop()

        assert location.startswith("http://"), (
            "expected the untrusted-proxy configuration to still downgrade; if this "
            "fails the test above no longer demonstrates anything"
        )


class TestTheDeployedImageSetsIt:
    """The test above proves the mechanism. This one pins the production lever,
    because nothing else in the repo would notice it being dropped."""

    def test_the_dockerfile_trusts_the_proxy(self):
        dockerfile = (REPO_ROOT / "Dockerfile").read_text()

        assert "FORWARDED_ALLOW_IPS" in dockerfile, (
            "the deployed image no longer sets FORWARDED_ALLOW_IPS, so uvicorn "
            "falls back to trusting only 127.0.0.1 and every redirect behind "
            "Railway's proxy downgrades to http://"
        )

    def test_it_is_not_set_as_an_unquoted_glob_on_the_command_line(self):
        """`sh -c` expands an unquoted * against WORKDIR /app, and uvicorn then
        exits at startup on the resulting file list -- the container never serves
        traffic. Keeping it out of the CMD string avoids the shell entirely."""
        dockerfile = (REPO_ROOT / "Dockerfile").read_text()

        assert "--forwarded-allow-ips=*" not in dockerfile
        assert "--forwarded-allow-ips *" not in dockerfile


class TestTheFrontEndDoesNotRelyOnARedirect:
    """Canonical URLs are the second half. Even with the scheme fixed, depending
    on a redirect costs every caller an extra round trip, and the three call
    sites below were all pointed at the redirecting form."""

    TEMPLATES = REPO_ROOT / "src" / "templates"

    def test_no_template_requests_the_redirecting_url(self):
        offenders = []
        for path in sorted(self.TEMPLATES.glob("*.html")):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if "/api/anomalies?" in line:
                    offenders.append(f"{path.name}:{number}: {line.strip()}")

        assert not offenders, (
            "these request /api/anomalies without its trailing slash, which 307s:\n"
            + "\n".join(offenders)
        )

    def test_the_templates_directory_was_actually_searched(self):
        """Without this the test above passes just as happily on an empty glob."""
        assert len(list(self.TEMPLATES.glob("*.html"))) >= 5


class TestAnHttpErrorIsNotShownAsAnEmptyResult:
    """`fetch()` rejects only on a network failure, so an HTTP error status used
    to reach `data.anomalies || []` and render an empty modal -- a member whose
    request broke looked identical to a member with a clean record."""

    MEMBERS = REPO_ROOT / "src" / "templates" / "members.html"

    def test_the_member_anomalies_modal_checks_the_status(self):
        source = self.MEMBERS.read_text()
        assert source.strip(), "members.html is empty -- this test would pass on nothing"

        assert "response.ok" in source, (
            "openMemberAnomalies does not check the response status, so an HTTP "
            "error renders as 'no anomalies found'"
        )
