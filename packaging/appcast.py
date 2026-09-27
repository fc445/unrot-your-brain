# /// script
# requires-python = ">=3.11"
# dependencies = ["cryptography>=43"]
# ///
"""The Sparkle appcast the Mac app updates from (PR-36).

Every GitHub release adds itself here: build-dmg.yml signs the DMG it built and
appends an item to `appcast.xml` on the repo's `appcast` branch, which the app
reads from raw.githubusercontent.com (SUFeedURL in mac/Support/Info.plist).

    uv run packaging/appcast.py keygen <private-key-file>
    uv run packaging/appcast.py public
    uv run packaging/appcast.py sign <file>
    uv run packaging/appcast.py add --appcast appcast.xml --dmg Unrot-0.2.0.dmg \\
        --tag v0.2.0 --build 57 --channel prod --url <download> --notes-url <release page>

`keygen` writes the private key to a file and prints the public key: the public
half is SPARKLE_PUBLIC_ED_KEY in the Xcode project, the private half the
SPARKLE_ED_PRIVATE_KEY repo secret. `public`, `sign` and `add` read the private
key from that environment variable; `public` prints its public half, which CI
checks against the project before publishing anything.

Signing is Sparkle's EdDSA: Ed25519 over the whole file, base64. The key is the
base64 of the 32-byte Ed25519 seed, the format `sign_update --ed-key-file`
reads, so a key made here also works with Sparkle's own tools. Done here rather
than with Sparkle's `sign_update` so the appcast job can run on Linux without
downloading Sparkle.

Every item carries a channel, `dev` or `prod`, and a build only accepts its own
(mac/UnrotMac/Update/Updater.swift). Sparkle offers items with no channel to
every build, so an item without one would reach both.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from dataclasses import dataclass
from email.utils import format_datetime
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

SPARKLE = "http://www.andymatuschak.org/xml-namespaces/sparkle"
ET.register_namespace("sparkle", SPARKLE)

CHANNELS = ("dev", "prod")
KEY_ENV = "SPARKLE_ED_PRIVATE_KEY"
# Releases kept per channel. Sparkle only ever wants the newest one it may
# install; the rest are history, and this keeps the file from growing forever.
KEEP = 20


def _s(tag: str) -> str:
    return f"{{{SPARKLE}}}{tag}"


# --- keys and signatures ------------------------------------------------------------


def keygen() -> tuple[str, str]:
    """A fresh key pair as (private, public), both base64."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(seed).decode(), base64.b64encode(public).decode()


def _load(private_key: str):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    raw = base64.b64decode(private_key.strip(), validate=True)
    if len(raw) == 64:
        raw = raw[:32]  # seed followed by public key, as libsodium keeps it
    if len(raw) != 32:
        raise ValueError(
            f"expected a base64 32-byte Ed25519 seed, got {len(raw)} bytes. "
            "Sparkle's older 96-byte key format cannot be read here; make a new key with `keygen`."
        )
    return Ed25519PrivateKey.from_private_bytes(raw)


def public_key(private_key: str) -> str:
    """The SUPublicEDKey that goes with `private_key`."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    return base64.b64encode(_load(private_key).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()


def sign(data: bytes, private_key: str) -> str:
    """Sparkle's sparkle:edSignature for `data`."""
    return base64.b64encode(_load(private_key).sign(data)).decode()


# --- the appcast --------------------------------------------------------------------


@dataclass(frozen=True)
class Release:
    tag: str  # v0.2.0 or v0.2.0-dev.1; also the item's guid
    build: int  # CFBundleVersion, which is what Sparkle compares
    channel: str
    url: str
    length: int
    signature: str
    notes_url: str
    published: datetime

    @property
    def display_version(self) -> str:
        return self.tag.removeprefix("v")


EMPTY = f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="{SPARKLE}">
  <channel>
    <title>unrot</title>
  </channel>
</rss>
"""


def add(appcast: str | None, release: Release) -> str:
    """`appcast` with `release` in it.

    Replaces an item with the same tag (a release rebuilt for the same tag), keeps
    the newest KEEP per channel, and lists newest first.
    """
    if release.channel not in CHANNELS:
        raise ValueError(f"channel must be one of {CHANNELS}, not {release.channel!r}")
    root = ET.fromstring(appcast or EMPTY)
    channel = root.find("channel")
    if channel is None:
        raise ValueError("not an appcast: no <channel>")

    items = [i for i in channel.findall("item") if i.findtext("guid") != release.tag]
    for item in channel.findall("item"):
        channel.remove(item)
    items.append(_item(release))
    items.sort(key=lambda i: int(i.findtext(_s("version")) or 0), reverse=True)

    kept: dict[str, int] = {}
    for item in items:
        name = item.findtext(_s("channel")) or ""
        kept[name] = kept.get(name, 0) + 1
        if kept[name] <= KEEP:
            channel.append(item)

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def _item(release: Release) -> ET.Element:
    item = ET.Element("item")
    ET.SubElement(item, "title").text = f"unrot {release.display_version}"
    ET.SubElement(item, "guid", isPermaLink="false").text = release.tag
    ET.SubElement(item, "pubDate").text = format_datetime(release.published)
    ET.SubElement(item, _s("version")).text = str(release.build)
    ET.SubElement(item, _s("shortVersionString")).text = release.display_version
    ET.SubElement(item, _s("channel")).text = release.channel
    ET.SubElement(item, _s("fullReleaseNotesLink")).text = release.notes_url
    ET.SubElement(
        item,
        "enclosure",
        {
            "url": release.url,
            "length": str(release.length),
            "type": "application/octet-stream",
            _s("edSignature"): release.signature,
        },
    )
    return item


# --- command line -------------------------------------------------------------------


def _private_key() -> str:
    key = os.environ.get(KEY_ENV, "").strip()
    if not key:
        sys.exit(f"appcast: {KEY_ENV} is not set")
    return key


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    k = commands.add_parser("keygen", help="make a key pair; print the public key")
    k.add_argument("private_key_file", type=Path)

    commands.add_parser("public", help="print the public key for the private key in the environment")

    s = commands.add_parser("sign", help="print a file's signature, as sign_update does")
    s.add_argument("file", type=Path)

    a = commands.add_parser("add", help="sign a DMG and add it to the appcast")
    a.add_argument("--appcast", type=Path, required=True, help="read if it exists, then written")
    a.add_argument("--dmg", type=Path, required=True)
    a.add_argument("--tag", required=True)
    a.add_argument("--build", type=int, required=True)
    a.add_argument("--channel", choices=CHANNELS, required=True)
    a.add_argument("--url", required=True, help="where the app downloads the DMG from")
    a.add_argument("--notes-url", required=True)

    args = parser.parse_args(argv)

    if args.command == "keygen":
        if args.private_key_file.exists():
            sys.exit(f"appcast: {args.private_key_file} exists; not overwriting a key")
        private, public = keygen()
        args.private_key_file.touch(mode=0o600)
        args.private_key_file.write_text(private + "\n")
        print(public)
    elif args.command == "public":
        print(public_key(_private_key()))
    elif args.command == "sign":
        data = args.file.read_bytes()
        print(f'sparkle:edSignature="{sign(data, _private_key())}" length="{len(data)}"')
    else:
        data = args.dmg.read_bytes()
        release = Release(
            tag=args.tag,
            build=args.build,
            channel=args.channel,
            url=args.url,
            length=len(data),
            signature=sign(data, _private_key()),
            notes_url=args.notes_url,
            published=datetime.now(timezone.utc),
        )
        existing = args.appcast.read_text() if args.appcast.exists() else None
        args.appcast.write_text(add(existing, release))
        print(f"appcast: {release.tag} (build {release.build}, {release.channel}) added to {args.appcast}")


if __name__ == "__main__":
    main()
