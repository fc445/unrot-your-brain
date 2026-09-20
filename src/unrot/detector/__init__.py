"""Detector: propose candidate gaps from captured sessions.

Hands candidates to the resolver (PR-21), which is the single write path into
the graph. This package never writes an event and never imports the event log --
a detector that can write its own findings is a detector whose findings are not
reviewable.
"""

from .candidates import IMPORTANCE, SIGNALS, Candidate, DetectionResult
from .detect import DETECTOR_VERSION, detect, detector_version
from .model import DEFAULT_BASE_URL, DEFAULT_MODEL, ModelConfig, build_proposer
from .windows import Window, build_windows, chunk_windows, group_windows

__all__ = [
    "Candidate",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "DETECTOR_VERSION",
    "DetectionResult",
    "IMPORTANCE",
    "ModelConfig",
    "SIGNALS",
    "Window",
    "build_proposer",
    "build_windows",
    "chunk_windows",
    "group_windows",
    "detect",
    "detector_version",
]
