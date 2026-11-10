"""Verify the privilege and browser boundaries around configurable base URLs."""

import httpx
import pytest
from fastapi import FastAPI
from open_webui.routers import configs, ollama, tools
from open_webui.utils import auth

from .conftest import INTERNAL
