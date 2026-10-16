"""v19.0.2 — bridge Host/Origin rebinding guards."""
import json
import threading
import time
import urllib.request

from airmouse.browser_bridge import BrowserBridgeServer


def _server():
    s = BrowserBridgeServer(port=0)
    assert s.start() is True
    time.sleep(0.05)
    return s


def test_health_ok_with_loopback_host():
    s = _server()
    try:
        port = s.port
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/health",
            headers={"Host": f"127.0.0.1:{port}"})
        with urllib.request.urlopen(req, timeout=5) as r:
            assert json.loads(r.read())["ok"] is True
    finally:
        s.stop()


def test_rebind_host_refused():
    s = _server()
    try:
        port = s.port
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/state",
            headers={"Host": "attacker.example.com"})
        try:
            urllib.request.urlopen(req, timeout=5)
            assert False, "rebinding Host must be refused"
        except urllib.error.HTTPError as e:
            assert e.code == 403
    finally:
        s.stop()


def test_foreign_origin_refused():
    s = _server()
    try:
        port = s.port
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/state",
            method="POST",
            data=json.dumps({"title": "x"}).encode(),
            headers={"Host": f"127.0.0.1:{port}",
                     "Origin": "https://attacker.example.com",
                     "Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=5)
            assert False, "foreign Origin must be refused"
        except urllib.error.HTTPError as e:
            assert e.code == 403
    finally:
        s.stop()


def test_extension_origin_accepted():
    s = _server()
    try:
        port = s.port
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/state",
            method="POST",
            data=json.dumps({"title": "t", "url": "https://x"}).encode(),
            headers={"Host": f"127.0.0.1:{port}",
                     "Origin": "chrome-extension://abcdef",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            assert json.loads(r.read())["ok"] is True
    finally:
        s.stop()
