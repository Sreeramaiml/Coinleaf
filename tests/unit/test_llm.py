import subprocess
import sys

from hypothesis import given
from hypothesis import strategies as st

from finassist.llm.provider import Usage
from finassist.llm.redact import redact_obj, redact_text
from finassist.llm.usage import estimate_micro_usd


def test_redacts_identifiers() -> None:
    s = (
        "NEFT CR-BNKA0009876-ACME-N123456789012 UPI/634512345678/RAVI/ravi.k@okaxis card XXXX XXXX XXXX 9013 "
        "acct XXXXXXXX4821 mail a.b@example.com PAN ABCDE1234F phone +91 98765 43210 9876543210"
    )
    out, n = redact_text(s)
    for leaked in (
        "BNKA0009876",
        "123456789012",
        "634512345678",
        "ravi.k@okaxis",
        "9013",
        "4821",
        "a.b@example.com",
        "ABCDE1234F",
        "98765",
        "9876543210",
    ):
        assert leaked not in out, leaked
    assert n >= 9


def test_keeps_amounts_dates_and_ids() -> None:
    s = "₹1,48,000.00 on 2026-10-01 (q_7f3a9c), 36 transactions, 1–31 Oct 2026"
    assert redact_text(s) == (s, 0)


def test_redact_obj_leaves_integers_and_keys() -> None:
    obj = {"sum": {"minor": 14800000, "display": "₹1,48,000.00"}, "desc": ["UPI/634512345678/X"]}
    out, n = redact_obj(obj)
    assert out["sum"] == obj["sum"] and out["desc"] == ["UPI/[NUM]/X"] and n == 1


@given(st.text(alphabet="0123456789", min_size=8, max_size=20), st.text(alphabet="ABCDEF /-", max_size=10))
def test_no_long_digit_run_survives(digits: str, noise: str) -> None:
    out, _ = redact_text(noise + digits + noise)
    assert digits not in out


def test_cost_estimate() -> None:
    # 1M input + 1M output on Opus 5.5 = $4 + $20
    assert estimate_micro_usd("claude-opus-5-5", Usage(1_000_000, 1_000_000)) == 24_000_000
    assert estimate_micro_usd("unknown-model", Usage(10, 10)) == 0


def _run(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)


def test_netguard_strict_blocks_everything_but_loopback() -> None:
    """Runs in a subprocess: an audit hook can never be removed."""
    r = _run(
        "import socket\n"
        "from finassist.llm import netguard\n"
        "netguard.install('strict')\n"
        "s = socket.socket(); s.bind(('127.0.0.1', 0)); s.listen(); port = s.getsockname()[1]\n"
        "c = socket.create_connection(('127.0.0.1', port), timeout=2); c.close(); print('loopback ok')\n"
        "for target in [('93.184.215.14', 80), ('api.anthropic.com', 443)]:\n"
        "    try:\n"
        "        socket.create_connection(target, timeout=2); print('LEAK', target)\n"
        "    except PermissionError as e: print('blocked')\n"
        "try:\n"
        "    netguard.install('cloud', ['api.anthropic.com']); print('LOOSENED')\n"
        "except PermissionError: print('stays strict')\n"
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["loopback", "ok", "blocked", "blocked", "stays", "strict"]


def test_netguard_cloud_allows_only_listed_host_lookups() -> None:
    r = _run(
        "import socket\n"
        "from finassist.llm import netguard\n"
        "netguard.install('cloud', ['api.anthropic.com'])\n"
        "for host in ['example.com', '93.184.215.14']:\n"
        "    try:\n"
        "        socket.getaddrinfo(host, 443); print('LEAK')\n"
        "    except PermissionError: print('blocked')\n"
        "try:\n"
        "    s = socket.socket(); s.settimeout(1); s.connect(('93.184.215.14', 443)); print('LEAK')\n"
        "except PermissionError: print('blocked')\n"
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["blocked", "blocked", "blocked"]
