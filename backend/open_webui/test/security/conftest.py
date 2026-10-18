"""Keep app imports in this suite away from the real static assets.

config.py empties STATIC_DIR on import and refills it from the frontend build.
In a checkout that directory is tracked source; in the CI container it is the
running app's own static directory. Set before any test imports the app, and
leave an explicit STATIC_DIR alone.
"""

import os
import tempfile

os.environ.setdefault('STATIC_DIR', tempfile.mkdtemp(prefix='owui-security-static-'))
