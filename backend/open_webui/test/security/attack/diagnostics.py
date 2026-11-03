"""Report blocking phases without request bodies, credentials or concrete URLs."""

import time
from contextlib import contextmanager


@contextmanager
def timed(label, *, announce=False):
    started = time.monotonic()
    if announce:
        print(f'Sweep starting: {label}', flush=True)
    try:
        yield
    finally:
        elapsed = time.monotonic() - started
        if announce or elapsed >= 5:
            print(f'Sweep elapsed: {elapsed:.3f}s: {label}', flush=True)
