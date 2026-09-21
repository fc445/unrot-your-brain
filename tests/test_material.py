"""Tests for learning material (PR-25).

Two things are being defended here, and they are the two the ticket says are not
"deliberately crude" like the rest of the skeleton.

**Provenance.** The product explains precisely the things the user cannot
evaluate, so an unsupported sentence lands on someone with no way to catch it
and ends up wrong in their head *and* in the graph. Every test about refusing to
write is really a test that the tool would rather say nothing.

**Depth 1.** Material names about five concepts. If each of those generated its
own, that is 125 by depth 3, none of it originating with the user. The guard is
one rule — no encounters, no generation — and these check it holds from both
directions.
"""

from __future__ import annotations

import json

import pytest

from unrot.material import (
    SOURCES_ONLY,
    TEXTUAL,
    NotGrounded,
    Source,
    WouldRecurse,
    deliver,
    for_concept,
    from_code,
    sources_only,
    textual,
    verify_web,
)
from unrot.resolver import manual, resolve, resolve_reference, strict
from unrot.store import compile_state, connect


@pytest.fixture
def conn():
    connection = connect()
    yield connection
    connection.close()


@pytest.fixture
def gap(conn):
    """A concept the user actually met. The only kind material may be made for."""
    return resolve(
        conn, manual("backpressure", "A bounded queue in the ingest service."), decide=strict
    )


def verified(n=2):
    return [
        Source(
            kind="web",
            ref=f"https://example.test/{i}",
            title=f"Source {i}",
            excerpt="A bounded queue makes a slow consumer push back on the producer.",
            verified=True,
        )
        for i in range(1, n + 1)
    ]


def writer(body, covers=()):
    def write(prompt_text):
        del prompt_text
        return {"body": body, "covers": list(covers)}

    return write


# ---------------------------------------------------------------------------
# Provenance: the tool would rather say nothing
# ---------------------------------------------------------------------------


def test_prose_is_refused_when_nothing_can_ground_it(conn, gap):
    """The P0 gate. No verified sources means no explanation, not a vaguer one."""
    unverified = [Source(kind="web", ref="https://x.test", title="x", verified=False)]
    with pytest.raises(NotGrounded, match="nothing verifiable"):
        textual(conn, gap.concept_id, unverified, write=writer("anything [S1]"))

    assert conn.execute("SELECT count(*) FROM compiled_material").fetchone()[0] == 0


def test_an_invented_citation_discards_the_whole_material(conn, gap):
    """A citation pointing at a source that was never supplied is the defect.

    Not trimmed, not repaired -- discarded. A plausible-looking reference that
    supports nothing is worse than no explanation, because the reader's only
    check is that a citation appears to be there.
    """
    with pytest.raises(NotGrounded, match="invented citation"):
        textual(
            conn,
            gap.concept_id,
            verified(2),
            write=writer("Backpressure bounds a queue [S1] and also [S9]."),
        )
    assert conn.execute("SELECT count(*) FROM compiled_material").fetchone()[0] == 0


def test_prose_citing_nothing_at_all_is_refused(conn, gap):
    with pytest.raises(NotGrounded, match="cited none"):
        textual(conn, gap.concept_id, verified(2), write=writer("Just trust me."))


def test_only_verified_sources_reach_the_writer(conn, gap):
    """An unverified link may be shown to a human; it may not back a sentence."""
    seen = {}

    def write(prompt_text):
        seen["prompt"] = prompt_text
        return {"body": "Bounded queues push back [S1]."}

    mixed = verified(1) + [
        Source(kind="web", ref="https://nope.test", title="Unreachable", verified=False)
    ]
    result = textual(conn, gap.concept_id, mixed, write=write)

    assert "https://nope.test" not in seen["prompt"]
    assert [s.ref for s in result.sources] == ["https://example.test/1"]


def test_sources_only_generates_nothing_and_so_can_invent_nothing(conn, gap):
    """Format 2 is a shipped format, not a fallback: it sidesteps the risk."""
    result = sources_only(conn, gap.concept_id, verified(2))

    assert result.format == SOURCES_ONLY
    assert result.body is None
    row = conn.execute("SELECT * FROM compiled_material").fetchone()
    assert row["body"] is None
    assert len(json.loads(row["sources"])) == 2


def test_sources_only_keeps_unverified_links_and_labels_them(conn, gap):
    """The user can click a link and judge it. They cannot do that to a sentence.

    So the bar for showing a pointer is deliberately lower than the bar for
    asserting something -- the reader's own check replaces ours.
    """
    mixed = verified(1) + [
        Source(
            kind="web", ref="https://nope.test", title="Unreachable",
            verified=False, note="could not be reached (URLError)",
        )
    ]
    result = sources_only(conn, gap.concept_id, mixed)
    stored = json.loads(
        conn.execute("SELECT sources FROM compiled_material").fetchone()["sources"]
    )
    assert len(result.sources) == 2
    assert [s["verified"] for s in stored] == [True, False]
    assert stored[1]["note"]


def test_sources_only_still_refuses_when_there_is_nothing_at_all(conn, gap):
    with pytest.raises(NotGrounded, match="nothing to point at"):
        sources_only(conn, gap.concept_id, [])


# ---------------------------------------------------------------------------
# Depth 1
# ---------------------------------------------------------------------------


def test_a_concept_named_in_material_never_generates_its_own(conn, gap):
    """The explosion guard, from the direction it would actually break.

    `quorum` is in the graph only because some material mentioned it. Letting it
    generate would be the recursive step, and there is no natural place to stop
    once it is allowed once.
    """
    named = resolve_reference(conn, "quorum", decide=strict)
    row = conn.execute(
        "SELECT * FROM compiled_concepts WHERE concept_id = ?", (named.concept_id,)
    ).fetchone()
    assert row["state"] == "referenced"
    assert row["encounter_count"] == 0

    with pytest.raises(WouldRecurse, match="never encountered"):
        textual(conn, named.concept_id, verified(2), write=writer("x [S1]"))
    with pytest.raises(WouldRecurse):
        sources_only(conn, named.concept_id, verified(2))


def test_material_puts_the_concepts_it_names_into_the_graph(conn, gap):
    """Many-to-many, because an explanation of one thing necessarily covers others."""
    def resolve_named(term):
        return resolve_reference(conn, term, decide=strict, recompile=False)

    result = textual(
        conn,
        gap.concept_id,
        verified(2),
        write=writer("Bounded queues push back [S1].", covers=["flow control", "buffering"]),
        resolve_named=resolve_named,
    )
    compile_state(conn)

    covered = {
        row["concept_id"]
        for row in conn.execute(
            "SELECT concept_id FROM compiled_material_concepts WHERE material_id = ?",
            (result.material_id,),
        )
    }
    assert gap.concept_id in covered
    assert len(covered) == 3

    states = {
        row["canonical_name"]: row["state"]
        for row in conn.execute("SELECT * FROM compiled_concepts")
    }
    # Named but never met: scaffolding, never surfaced.
    assert states["flow control"] == "referenced"
    assert states["buffering"] == "referenced"


def test_naming_is_capped_so_one_step_stays_one_step(conn, gap):
    def resolve_named(term):
        return resolve_reference(conn, term, decide=strict, recompile=False)

    result = textual(
        conn,
        gap.concept_id,
        verified(2),
        write=writer("x [S1]", covers=[f"concept {i}" for i in range(12)]),
        resolve_named=resolve_named,
    )
    assert len(result.covered) == 5


def test_a_concept_the_user_met_becomes_exposed_once_material_is_delivered(conn, gap):
    """The third state: taught at, but never confirmed and not untouched."""
    result = sources_only(conn, gap.concept_id, verified(2))
    assert conn.execute("SELECT state FROM compiled_concepts").fetchone()["state"] == "gap"

    deliver(conn, result.material_id)
    assert conn.execute("SELECT state FROM compiled_concepts").fetchone()["state"] == "exposed"


def test_generating_without_delivering_leaves_no_mark(conn, gap):
    """Made and never shown must not count as having been taught."""
    sources_only(conn, gap.concept_id, verified(2))
    assert conn.execute("SELECT state FROM compiled_concepts").fetchone()["state"] == "gap"


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def test_the_users_own_code_is_searched_not_guessed_at(conn, tmp_path):
    """A model naming a file in someone's repo is guessing.

    A citation pointing at a file that does not exist is the same defect as an
    invented URL, so code sources are found by reading the disk.
    """
    (tmp_path / "ingest.py").write_text(
        "def consume(queue):\n    # backpressure: bounded queue, producer waits\n    ...\n",
        encoding="utf-8",
    )
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "vendor.js").write_text("backpressure", encoding="utf-8")

    found = from_code(tmp_path, "backpressure")
    assert [s.ref for s in found] == ["ingest.py:2"]
    assert found[0].verified is True
    assert "bounded queue" in found[0].excerpt


def test_a_page_that_does_not_mention_the_term_is_not_a_source_for_it(conn, monkeypatch):
    """A 200 is not evidence. A citation has to support the thing it is next to."""
    import contextlib
    import io

    from unrot.material import sources as sources_module

    class Response(io.BytesIO):
        status = 200

    @contextlib.contextmanager
    def fake(request, timeout=None):
        del request, timeout
        yield Response(b"<html><body>An article about kittens.</body></html>")

    monkeypatch.setattr(sources_module.urllib.request, "urlopen", fake)
    checked = verify_web(
        Source(kind="web", ref="https://x.test", title="x"), term="backpressure"
    )
    assert checked.verified is False
    assert "does not mention" in checked.note


def test_an_unreachable_page_is_kept_but_marked(conn, monkeypatch):
    import contextlib

    from unrot.material import sources as sources_module

    @contextlib.contextmanager
    def fake(request, timeout=None):
        del request, timeout
        raise OSError("boom")

    monkeypatch.setattr(sources_module.urllib.request, "urlopen", fake)
    checked = verify_web(
        Source(kind="web", ref="https://x.test", title="x"), term="backpressure"
    )
    assert checked.verified is False
    assert "could not be reached" in checked.note


# ---------------------------------------------------------------------------
# Both formats, and switching between them
# ---------------------------------------------------------------------------


def test_one_gap_can_have_material_in_both_formats(conn, gap):
    """The named v1 acceptance criterion."""
    sources_only(conn, gap.concept_id, verified(2))
    textual(conn, gap.concept_id, verified(2), write=writer("Queues push back [S1]."))

    made = for_concept(conn, gap.concept_id)
    assert {row["format"] for row in made} == {TEXTUAL, SOURCES_ONLY}


def test_material_survives_a_recompile(conn, gap):
    result = textual(
        conn, gap.concept_id, verified(2), write=writer("Queues push back [S2].")
    )
    deliver(conn, result.material_id)
    compile_state(conn)
    compile_state(conn)

    row = conn.execute("SELECT * FROM compiled_material").fetchone()
    assert row["body"] == "Queues push back [S2]."
    assert row["delivered_at"]
    assert conn.execute("SELECT state FROM compiled_concepts").fetchone()["state"] == "exposed"


def test_nothing_generates_on_its_own(conn, gap):
    """Turning material off has to leave a fully useful product.

    Nothing here runs in the background, so "off" is the default and the gap
    graph stands alone -- which is what makes material a pluggable output stage
    rather than the point of the thing.
    """
    assert conn.execute("SELECT count(*) FROM compiled_material").fetchone()[0] == 0
    assert conn.execute("SELECT state FROM compiled_concepts").fetchone()["state"] == "gap"
