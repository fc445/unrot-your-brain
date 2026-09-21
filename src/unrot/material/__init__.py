"""Learning material: two formats, grounded, expanding one step and no further.

The gap graph is the product; this is a pluggable output stage on top of it.
Turning it off entirely has to leave a fully useful tool, which is why nothing
here runs on its own -- material is made when it is asked for, never in the
background.
"""

from .generate import (
    FORMATS,
    MAX_COVERED,
    SOURCES_ONLY,
    TEXTUAL,
    Material,
    NotGrounded,
    WouldRecurse,
    deliver,
    for_concept,
    material_version,
    sources_only,
    textual,
)
from .search import build_search
from .sources import (
    Source,
    from_code,
    from_transcript,
    gather,
    repo_for,
    verify_web,
)
from .writer import build_writer

__all__ = [
    "FORMATS",
    "MAX_COVERED",
    "Material",
    "NotGrounded",
    "SOURCES_ONLY",
    "Source",
    "TEXTUAL",
    "WouldRecurse",
    "build_search",
    "build_writer",
    "deliver",
    "for_concept",
    "from_code",
    "from_transcript",
    "gather",
    "material_version",
    "repo_for",
    "sources_only",
    "textual",
    "verify_web",
]
