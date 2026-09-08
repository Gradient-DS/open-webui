"""Print clean defaults in a fresh process under the running CI container's env.

This does not import main, run startup, or read/write the live config store.
Use only in the sealed CI stack; importing config still needs backend packages.
"""

import contextlib
import json
import os
import sys
from tempfile import TemporaryDirectory


def main():
    with TemporaryDirectory(prefix='plane-config-static-') as static_dir:
        os.environ['STATIC_DIR'] = static_dir
        os.environ['ENABLE_DB_MIGRATIONS'] = 'false'
        with contextlib.redirect_stdout(sys.stderr):
            from open_webui.config import DEFAULT_CONFIG
        print(json.dumps(DEFAULT_CONFIG, sort_keys=True))


if __name__ == '__main__':
    main()
