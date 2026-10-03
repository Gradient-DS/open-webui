"""[Gradient] Importing `open_webui.config` empties STATIC_DIR and refills it from the frontend build. A worktree
has no build, so a test run deleted the tracked files in `backend/open_webui/static/`; tests get their own."""

import os
import tempfile

os.environ.setdefault('STATIC_DIR', tempfile.mkdtemp(prefix='owui-test-static-'))
