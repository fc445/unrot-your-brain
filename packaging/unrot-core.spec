# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the sidecar the Mac app bundles and supervises.

Build:  uv run --group packaging pyinstaller packaging/unrot-core.spec --noconfirm

`--onedir`, not `--onefile`. A one-file bundle unpacks itself into a temp
directory on every launch, which costs a second or two of startup the user
would watch, and puts the interpreter somewhere outside the app bundle where
the code signature does not reach. The Mac app co-signs this directory as a
bundled resource; it is never installed and never on PATH.

Two things here are load-bearing rather than boilerplate, and both fail the
same way -- fine on this machine, broken on a machine with no Python:

* **The schema files.** `store/db.py` and `capture/ingest.py` each read a
  sibling `schema.sql` through `Path(__file__).with_name(...)`. PyInstaller
  collects modules, not arbitrary data, so without `datas` the frozen core
  starts happily and dies on its first `connect()`.
* **LangChain's imports.** `langchain_openai` resolves plenty of things
  dynamically, which static analysis cannot see.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

HERE = Path(SPECPATH).resolve()
SRC = HERE.parent / "src"

datas = [
    (str(SRC / "unrot" / "store" / "schema.sql"), "unrot/store"),
    (str(SRC / "unrot" / "capture" / "schema.sql"), "unrot/capture"),
]
binaries = []
hiddenimports = [
    # uvicorn resolves its own implementation classes by string at startup.
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    # The app is loaded by import string too ("unrot.api.app:app"), and the
    # subcommands are reached only through late imports inside endpoints.
    *collect_submodules("unrot"),
]

# LangChain is here for an OpenAI-compatible client, not as an agent framework
# -- but it still resolves providers and tokenisers dynamically, so it needs
# collecting whole rather than trusting the import graph.
for package in ("langchain_openai", "langchain_core", "tiktoken", "tiktoken_ext"):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    except Exception:  # noqa: BLE001 - an absent optional package is not a build failure
        continue
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

analysis = Analysis(
    [str(HERE / "core_entry.py")],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # The Mac app is the surface. Nothing in the sidecar renders anything, and
    # dragging a GUI toolkit into a bundle that serves JSON is pure weight.
    excludes=["tkinter", "matplotlib", "PIL", "pytest", "IPython", "numpy.testing"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="unrot-core",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-compressed binaries cannot be notarised.
    console=True,
    target_arch=None,  # follows the building interpreter; universal2 needs one too.
    codesign_identity=None,  # the Xcode build co-signs this with the app.
    entitlements_file=None,
)

COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="unrot-core",
)
