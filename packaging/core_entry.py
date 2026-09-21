"""The frozen sidecar's entry point.

PyInstaller freezes a *script*, not a `-m` target, so this exists to be that
script and to do nothing else. Everything it does is about the two ways a
frozen process differs from `python -m unrot.api`:

* **`sys.argv[0]` is the binary**, not a module, so `argparse`'s `prog` would
  otherwise read as whatever the bundle happens to be called.
* **There is no working directory worth trusting.** Launched by the Mac app,
  cwd is `/`. `load_env()` walks up from cwd looking for a `.env`, which in a
  frozen bundle means walking up from somewhere meaningless -- so the app
  passes configuration in the environment and the repo `.env` is simply absent.
  That is the correct behaviour, not a degradation: a shipped app should not be
  reading a developer's checkout.
"""

from __future__ import annotations

import multiprocessing
import sys


def main() -> int:
    # Without this a frozen process that ever spawns one of its own re-executes
    # the bundle from the top instead of starting a child, which presents as the
    # app launching itself repeatedly. Cheap insurance, and it must run before
    # anything else touches multiprocessing.
    multiprocessing.freeze_support()

    from unrot.api.__main__ import main as serve

    return serve()


if __name__ == "__main__":
    sys.exit(main())
