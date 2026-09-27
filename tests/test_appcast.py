"""Tests for packaging/appcast.py, the Sparkle feed releases add themselves to (PR-36).

The feed decides what every installed copy of the app is offered, so these pin
the guarantees the app relies on: every item has a channel (an item without one
reaches dev and prod builds alike), a rebuilt tag replaces its item rather than
adding a second, and Sparkle finds the build number where it compares versions.
"""

from __future__ import annotations

import base64
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

_path = Path(__file__).resolve().parents[1] / "packaging" / "appcast.py"
_spec = importlib.util.spec_from_file_location("appcast", _path)
appcast = importlib.util.module_from_spec(_spec)
sys.modules["appcast"] = appcast  # dataclasses look their module up there
_spec.loader.exec_module(appcast)

S = f"{{{appcast.SPARKLE}}}"


def release(tag: str, build: int, channel: str = "prod") -> "appcast.Release":
    return appcast.Release(
        tag=tag,
        build=build,
        channel=channel,
        url=f"https://example.invalid/{tag}/Unrot.dmg",
        length=1234,
        signature="c2lnbmF0dXJl",
        notes_url=f"https://example.invalid/releases/{tag}",
        published=datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc),
    )


def items(xml: str) -> list[ET.Element]:
    return ET.fromstring(xml).find("channel").findall("item")


def test_first_release_makes_a_feed_sparkle_can_read():
    xml = appcast.add(None, release("v0.2.0", 57))
    [item] = items(xml)
    assert item.findtext(f"{S}version") == "57"
    assert item.findtext(f"{S}shortVersionString") == "0.2.0"
    assert item.findtext(f"{S}channel") == "prod"
    assert item.findtext("guid") == "v0.2.0"
    enclosure = item.find("enclosure")
    assert enclosure.get("url") == "https://example.invalid/v0.2.0/Unrot.dmg"
    assert enclosure.get("length") == "1234"
    assert enclosure.get(f"{S}edSignature") == "c2lnbmF0dXJl"
    # The sparkle prefix itself, not ns0: Sparkle matches on the namespace, but
    # a feed people can read is worth one line.
    assert 'xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"' in xml
    assert "<sparkle:version>57</sparkle:version>" in xml


def test_every_item_keeps_its_channel_and_newest_comes_first():
    xml = appcast.add(None, release("v0.2.0-dev.1", 50, "dev"))
    xml = appcast.add(xml, release("v0.2.0", 57, "prod"))
    xml = appcast.add(xml, release("v0.3.0-dev.1", 61, "dev"))
    got = [(i.findtext("guid"), i.findtext(f"{S}channel")) for i in items(xml)]
    assert got == [("v0.3.0-dev.1", "dev"), ("v0.2.0", "prod"), ("v0.2.0-dev.1", "dev")]


def test_rebuilding_a_tag_replaces_its_item():
    xml = appcast.add(None, release("v0.2.0-dev.1", 50, "dev"))
    xml = appcast.add(xml, release("v0.2.0-dev.1", 50, "dev"))
    assert len(items(xml)) == 1


def test_old_releases_are_pruned_per_channel():
    xml = None
    for n in range(appcast.KEEP + 5):
        xml = appcast.add(xml, release(f"v0.{n}.0-dev.1", 100 + n, "dev"))
    xml = appcast.add(xml, release("v0.1.0", 1, "prod"))
    dev = [i for i in items(xml) if i.findtext(f"{S}channel") == "dev"]
    assert len(dev) == appcast.KEEP
    assert dev[0].findtext(f"{S}version") == str(100 + appcast.KEEP + 4)
    # A prod release older than every dev one is not pushed out by them.
    assert [i.findtext("guid") for i in items(xml)][-1] == "v0.1.0"


def test_an_item_without_a_known_channel_is_refused():
    with pytest.raises(ValueError, match="channel"):
        appcast.add(None, release("v0.2.0", 57, "beta"))


def test_signature_is_ed25519_and_verifies_with_the_public_key():
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    private, public = appcast.keygen()
    assert len(base64.b64decode(private)) == 32  # the seed, as sign_update reads it
    assert appcast.public_key(private) == public
    data = b"not really a disk image"
    signature = appcast.sign(data, private)
    Ed25519PublicKey.from_public_bytes(base64.b64decode(public)).verify(base64.b64decode(signature), data)


def test_old_sparkle_key_format_gets_a_clear_error():
    pytest.importorskip("cryptography")
    with pytest.raises(ValueError, match="96-byte"):
        appcast.sign(b"x", base64.b64encode(bytes(96)).decode())
