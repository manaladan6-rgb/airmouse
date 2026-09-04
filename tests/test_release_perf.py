"""v15.1 release performance budgets (§16).

The hardening release adds user-facing commands (doctor / setup / test /
verify / privacy / memory).  These budgets fail CI if a regression makes
any release command pathologically slow.  Values are generous multiples
of the measured sandbox numbers (2026-09 baseline):

    CLI --version   ~1.4s   (dominated by cv2/mediapipe import)
    CLI doctor      ~2.6s   (full detection incl. one camera probe)
    CLI verify      ~1.6s   (10 automated checks)
    CLI test        ~1.6s   (non-interactive 12-test lab)

Budgets are set at ~4x the measured numbers to tolerate CI variance
while still catching real regressions (accidental heavy imports in hot
paths, detector hangs, etc.).
"""
import os
import subprocess
import sys
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PKG = os.path.join(_ROOT, "airmouse_pkg")


def _cli_time(args, budget_s, env_home):
    env = dict(os.environ)
    env["AIRMOUSE_HOME"] = env_home
    env["PYTHONPATH"] = _PKG + os.pathsep + env.get("PYTHONPATH", "")
    start = time.perf_counter()
    proc = subprocess.run([sys.executable, "-m", "airmouse"] + args,
                          capture_output=True, env=env, timeout=budget_s + 30)
    elapsed = time.perf_counter() - start
    return elapsed, proc


@pytest.mark.parametrize("args,budget,label", [
    (["--version"], 6.0, "CLI --version"),
    (["doctor"], 12.0, "CLI doctor"),
    (["verify"], 8.0, "CLI verify"),
    (["test"], 8.0, "CLI test (non-interactive)"),
])
def test_release_command_budgets(args, budget, label, tmp_path):
    elapsed, proc = _cli_time(args, budget, str(tmp_path / "am-perf"))
    assert proc.returncode in (0, 1, 2), \
        f"{label} crashed: rc={proc.returncode}"
    assert elapsed < budget, \
        f"{label} took {elapsed:.2f}s, budget {budget:.1f}s"


def test_lightweight_modules_stay_stdlib_only():
    """user_errors / cli_menu must not drag cv2/mediapipe into a process
    that only needs friendly errors or the menu (keeps error paths fast
    even on weak machines)."""
    code = (
        "import sys;"
        "import airmouse.user_errors, airmouse.cli_menu;"
        "heavy = [m for m in ('cv2', 'mediapipe', 'numpy') if m in sys.modules];"
        "print('HEAVY:' + ','.join(heavy))"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = _PKG + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run([sys.executable, "-c", code],
                          capture_output=True, env=env, timeout=30)
    out = proc.stdout.decode()
    assert proc.returncode == 0, proc.stderr.decode()
    assert out.strip() == "HEAVY:", f"heavy modules imported: {out}"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
