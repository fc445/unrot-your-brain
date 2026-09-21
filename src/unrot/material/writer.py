"""Building the `write` callable for format 1.

The same seam as everywhere else: `textual()` takes a callable, this builds one,
and the grounding and citation rules stay testable without a network.
"""

from __future__ import annotations

from ..model import ModelConfig, structured_client


def build_writer(config: ModelConfig | None = None):
    """Return `write(prompt_text) -> {"body": str, "covers": list[str]}`."""
    from pydantic import BaseModel, Field

    class Written(BaseModel):
        body: str = Field(
            description=(
                "The explanation. Every substantive claim followed by its source"
                " tag, like [S2]. Leave out anything the sources do not support."
            )
        )
        covers: list[str] = Field(
            default_factory=list,
            description=(
                "Other concepts a reader needs in order to follow this, at most"
                " five. Only ones actually leaned on. Empty is a fine answer."
            ),
        )

    client = structured_client(config or ModelConfig.from_env(), Written)

    def write(prompt_text: str) -> dict:
        return client.invoke(prompt_text).model_dump()

    return write
