"""
airmouse_agent — the lightweight standalone agent runtime
(airmouse-agent-core, v14.5 §11).

DESIGN GOALS (§11):
    * minimal dependencies  — stdlib ONLY, zero third-party imports
    * fast import           — no heavy work at module load
    * low memory            — no caches, no threads until used
    * lazy loading          — transports import on first use
    * no cloud requirement  — local transports only
    * no camera / microphone / GUI / ML requirement
    * speaks AIP (AirMouse Interaction Protocol) messages

An agent that only needs computer interaction must not load the
entire AirMouse perception stack — this package never imports
``airmouse``.

Copyright (c) AirMouse.  MIT License.
"""

from .client import AirMouse, AipError
from .version import AGENT_CORE_VERSION, AIP_VERSION_SUPPORTED

__all__ = ["AirMouse", "AipError", "AGENT_CORE_VERSION",
           "AIP_VERSION_SUPPORTED"]
