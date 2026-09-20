"""ULID minting — sortable, coordination-free identifiers.

Two properties matter here, both structural:

* **Sortable by time.** The compile step is a fold, and a fold needs a total
  order. Sorting by `event_id` alone gives chronological order for free.
* **Minted without coordination.** S7 says logs from two machines merge with no
  locking. Two ULIDs generated on different machines in the same millisecond
  will not collide (80 bits of randomness) and will still interleave correctly.

Crockford base32, stdlib only.
"""

from __future__ import annotations

import os
import threading
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford: no I, L, O, U


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        value, rem = divmod(value, 32)
        chars.append(_ALPHABET[rem])
    return "".join(reversed(chars))


_RANDOM_BITS = 80
_MAX_RANDOM = (1 << _RANDOM_BITS) - 1

_lock = threading.Lock()
_last_ms = -1
_last_random = 0


def new_ulid(when_ms: int | None = None) -> str:
    """Return a 26-character ULID: 10 chars of timestamp, 16 of randomness.

    Monotonic within a millisecond. This is not a nicety -- the compile step
    folds events in `event_id` order, and several events routinely land in the
    same millisecond (record an encounter, confirm it, submit an explanation).
    Without monotonicity those would sort by their random half instead of by
    insertion order, and the fold would be non-deterministic: the same log could
    compile to a confirmation arriving before the encounter it confirms.

    So within a millisecond we increment the previous random value rather than
    drawing a fresh one, per the ULID spec's monotonic variant.
    """
    global _last_ms, _last_random

    ms = int(time.time() * 1000) if when_ms is None else when_ms
    with _lock:
        if ms == _last_ms:
            _last_random = (_last_random + 1) & _MAX_RANDOM
        else:
            _last_ms = ms
            _last_random = int.from_bytes(os.urandom(10), "big")
        random_part = _last_random
    return _encode(ms, 10) + _encode(random_part, 16)


def ulid_timestamp_ms(ulid: str) -> int:
    """Recover the millisecond timestamp a ULID was minted at."""
    ms = 0
    for char in ulid[:10]:
        ms = ms * 32 + _ALPHABET.index(char)
    return ms
