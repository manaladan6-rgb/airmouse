"""airmouse.quiet — v16.5.1 §13: silence THIRD-PARTY diagnostic noise.

The real Windows run surfaced dependency chatter between the honest
lines of the teacher/academy output::

    Failed to send to clearcut          (MediaPipe's Google Clearcut
                                         telemetry uploader — a C++
                                         stderr write, NOT an AirMouse
                                         error; it fires on offline
                                         machines and is irrelevant)
    INFO: Created TensorFlow Lite XNNPACK delegate ...   (TFLite banner)
    W ... external/local_x/x/...        (TensorFlow/absl warnings)

Policy (§13): suppress third-party diagnostic noise WHERE SAFE —
*never* hide an actual AirMouse error.  Therefore:

1.  Environment dials (``GLOG_minloglevel``, ``TF_CPP_MIN_LOG_LEVEL``,
    ``ABSL_MIN_LOG_LEVEL``) are raised to ERROR **only if the user did
    not set them already** — their own tuning always wins.
2.  Python logging: the ``absl`` / ``tensorflow`` / ``mediapipe`` /
    ``tts_oss`` loggers are raised to ERROR on THEIR loggers only;
    the ``airmouse`` logger is untouched.
3.  absl's Python verbosity is lowered when absl is importable.
4.  The C++ Clearcut stderr line cannot be intercepted by Python
    logging — it happens inside MediaPipe before our filters exist.
    The env dials in (1) are what actually reach it; where they do not
    take effect (older MediaPipe builds) the line stays visible and we
    document it honestly in the Windows test guide instead of risking
    an fd-level stderr filter that could swallow real errors.

Call :func:`quiet_third_party_logs` once at process start (main()) and
at academy/teacher entry points; idempotent, never raises.
"""

from __future__ import annotations

import logging
import os

__all__ = ["quiet_third_party_logs", "THIRD_PARTY_LOGGERS"]

#: environment dials for the C/C++ layers (value = ERROR level)
_ENV_DIALS = {
    "GLOG_minloglevel": "3",          # glog: 3 = ERROR only
    "TF_CPP_MIN_LOG_LEVEL": "3",      # TensorFlow C++: 3 = nothing
    "ABSL_MIN_LOG_LEVEL": "3",        # absl C++ stderr noise
    # OpenCV C++ videoio WARN noise on machines without a camera
    # ("can't open camera by index" while the probe honestly reports the
    # result in AirMouse's own output).  ERROR keeps real OpenCV errors.
    "OPENCV_LOG_LEVEL": "ERROR",
    # v19.0.2 — the obsensor videoio backend emits native C++ "[ERROR]"
    # lines on affected machines even when AirMouse merely PROBES for a
    # camera and never opens the obsensor device.  Removing the backend
    # from the priority list (0 = disabled) keeps doctor/setup output
    # clean without hiding any real AirMouse error.
    "OPENCV_VIDEOIO_PRIORITY_OBSENSOR": "0",
}

#: third-party Python loggers that may be dialed down (never airmouse's)
THIRD_PARTY_LOGGERS = ("absl", "tensorflow", "mediapipe", "tflite_runtime",
                       "speech_recognition", "vosk", "pyttsx3", "comtypes",
                       "pocketsphinx")

_DONE = False


def quiet_third_party_logs() -> None:
    """Silence third-party diagnostic noise, idempotently, never raising.

    See the module docstring for the exact policy — in short: third-
    party dials and loggers only; AirMouse's own logging and every
    ``print`` of real errors stay untouched.
    """
    global _DONE
    if _DONE:
        return
    _DONE = True
    try:
        # 1) env dials — respected only when the user has not set them
        for key, val in _ENV_DIALS.items():
            if not os.environ.get(key, "").strip():
                os.environ[key] = val
        # 2) third-party python loggers → ERROR (their loggers only)
        for name in THIRD_PARTY_LOGGERS:
            try:
                logging.getLogger(name).setLevel(logging.ERROR)
            except Exception:
                continue
        # 3) absl python verbosity (if present)
        try:
            from absl import logging as _absl_logging
            _absl_logging.set_verbosity(_absl_logging.ERROR)
            _absl_logging.set_stderrthreshold(_absl_logging.ERROR)
        except Exception:
            pass
        # 4) the noisy "No handling of deferred data" style warnings from
        #    distutils/setuptools that sometimes leak via mediapipe imports
        try:
            import warnings as _warnings
            _warnings.filterwarnings(
                "ignore", message=".*deprecated and slated for removal.*",
                category=DeprecationWarning, module=r"airmouse\.offline_voice")
        except Exception:
            pass
    except Exception:
        # quieting must NEVER break the app
        return
