#!/usr/bin/env python3
"""
Bootstrap functions for Open WebUI deployments.

This script:
1. Creates admin user if none exists (first user becomes admin)
2. Authenticates to get API token
3. Syncs all pipe functions from the pipes directory

Usage:
    # Auto-load from .env in repo root (default behavior)
    python bootstrap_functions.py

    # Load credentials from specific .env file
    python bootstrap_functions.py --env-file ../../.env.neo

    # Using environment variables (recommended for K8s)
    OPENWEBUI_URL=http://open-webui:8080 \
    OPENWEBUI_ADMIN_EMAIL=admin@example.com \
    OPENWEBUI_ADMIN_PASSWORD=password \
    python bootstrap_functions.py

    # Or with API token (skip user creation)
    OPENWEBUI_URL=http://open-webui:8080 \
    OPENWEBUI_API_TOKEN=sk-xxx \
    python bootstrap_functions.py

    # Or with command line args
    python bootstrap_functions.py --url http://localhost:8080 --email admin@example.com --password secret

Dependencies:
    pip install python-dotenv requests
"""

import os
import sys
import time
import argparse
import requests
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv, dotenv_values


def wait_for_service(url: str, timeout: int = 120) -> bool:
    """Wait for Open WebUI to be ready."""
    print(f"Waiting for Open WebUI at {url}...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            response = requests.get(f"{url}/health", timeout=5)
            if response.status_code == 200:
                print("Open WebUI is ready!")
                return True
        except requests.exceptions.RequestException:
            pass
        time.sleep(2)
    return False


def create_admin_user(url: str, email: str, password: str, name: str = "Admin") -> bool:
    """Create admin user via signup (first user becomes admin)."""
    try:
        response = requests.post(
            f"{url}/api/v1/auths/signup",
            json={"email": email, "password": password, "name": name}
        )
        if response.status_code == 200:
            print(f"Admin user created: {email}")
            return True
        elif response.status_code == 403:
            # User already exists or signup disabled
            print(f"Signup not available (user may already exist)")
            return False
        else:
            print(f"Failed to create admin: {response.status_code} - {response.text[:200]}")
            return False
    except Exception as e:
        print(f"Failed to create admin user: {e}")
        return False


def get_api_token(url: str, email: str, password: str) -> Optional[str]:
    """Authenticate and get API token."""
    try:
        response = requests.post(
            f"{url}/api/v1/auths/signin",
            json={"email": email, "password": password}
        )
        response.raise_for_status()
        return response.json().get("token")
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            print(f"Authentication failed - invalid credentials for {email}")
        else:
            print(f"Failed to authenticate: {e}")
        return None
    except Exception as e:
        print(f"Failed to authenticate: {e}")
        return None


def load_pipe_files(pipes_dir: Path) -> list[dict]:
    """Load all pipe files from directory."""
    functions = []

    for pipe_file in pipes_dir.glob("*.py"):
        # Skip non-pipe files
        if pipe_file.name.startswith(("bootstrap", "register", "__")):
            continue

        content = pipe_file.read_text()

        # Extract metadata from docstring
        name = pipe_file.stem.replace("_", " ").title()
        description = ""

        # Parse docstring for title and description
        if '"""' in content:
            docstring = content.split('"""')[1]
            for line in docstring.strip().split("\n"):
                line = line.strip()
                if line.startswith("title:"):
                    name = line.replace("title:", "").strip()
                elif line.startswith("description:"):
                    description = line.replace("description:", "").strip()

        functions.append({
            "id": pipe_file.stem.lower(),
            "name": name,
            "content": content,
            "meta": {"description": description}
        })

    return functions


def sync_functions(url: str, token: str, functions: list[dict]) -> bool:
    """Sync functions to Open WebUI."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    success = True
    for func in functions:
        func_id = func["id"]

        # Check if function exists
        check_response = requests.get(
            f"{url}/api/v1/functions/id/{func_id}",
            headers=headers
        )

        if check_response.status_code == 200:
            # Update existing
            print(f"Updating function: {func['name']} ({func_id})")
            response = requests.post(
                f"{url}/api/v1/functions/id/{func_id}/update",
                json=func,
                headers=headers
            )
        else:
            # Create new
            print(f"Creating function: {func['name']} ({func_id})")
            response = requests.post(
                f"{url}/api/v1/functions/create",
                json=func,
                headers=headers
            )

        if response.status_code not in (200, 201):
            print(f"  Error: {response.status_code} - {response.text[:200]}")
            success = False
        else:
            result = response.json()
            # Activate if not active
            if not result.get("is_active"):
                requests.post(
                    f"{url}/api/v1/functions/id/{func_id}/toggle",
                    headers=headers
                )
            print(f"  Success! Active: {result.get('is_active', False)}")

    return success


def main():
    parser = argparse.ArgumentParser(description="Bootstrap Open WebUI functions")
    parser.add_argument("--env-file", help="Load config from .env file (e.g., ../../.env.neo)")
    parser.add_argument("--url", help="Open WebUI URL (default: http://localhost:8080)")
    parser.add_argument("--token", help="API token (skip authentication)")
    parser.add_argument("--email", help="Admin email for authentication")
    parser.add_argument("--password", help="Admin password for authentication")
    parser.add_argument("--name", help="Admin user display name")
    parser.add_argument("--pipes-dir", default=str(Path(__file__).parent))
    parser.add_argument("--wait", action="store_true", help="Wait for service to be ready")
    parser.add_argument("--timeout", type=int, default=120, help="Wait timeout in seconds")

    args = parser.parse_args()

    # Load env file - priority: --env-file arg > .env in repo root > system env
    if args.env_file:
        env_path = Path(args.env_file)
        if not env_path.is_absolute():
            env_path = Path(__file__).parent / env_path
        if env_path.exists():
            load_dotenv(env_path, override=True)
            env_vars = dotenv_values(env_path)
            print(f"Loaded {len(env_vars)} variables from {env_path}")
        else:
            print(f"Warning: env file not found: {env_path}")
    else:
        # Auto-discover .env from repo root (searches upward from script location)
        script_dir = Path(__file__).parent
        # Try to find .env in repo root (2 levels up from scripts/pipes/)
        repo_root = script_dir.parent.parent
        root_env = repo_root / ".env"
        if root_env.exists():
            load_dotenv(root_env, override=True)
            env_vars = dotenv_values(root_env)
            print(f"Auto-loaded {len(env_vars)} variables from {root_env}")
        else:
            # Fall back to python-dotenv's auto-discovery
            load_dotenv()

    # Priority: CLI args > environment variables (now includes loaded .env) > defaults
    def get_config(key: str, cli_value: str = None, default: str = None) -> str:
        if cli_value:
            return cli_value
        return os.getenv(key, default)

    # URL can come from OPENWEBUI_URL or be constructed from OPEN_WEBUI_PORT
    url = get_config("OPENWEBUI_URL", args.url)
    if not url:
        port = get_config("OPEN_WEBUI_PORT", None, "8080")
        url = f"http://localhost:{port}"
    url = url.rstrip("/")

    token = get_config("OPENWEBUI_API_TOKEN", args.token)
    email = get_config("OPENWEBUI_ADMIN_EMAIL", args.email)
    password = get_config("OPENWEBUI_ADMIN_PASSWORD", args.password)
    name = get_config("OPENWEBUI_ADMIN_NAME", args.name, "Admin")

    # Wait for service if requested (useful for K8s init containers)
    if args.wait:
        if not wait_for_service(url, args.timeout):
            print("Timeout waiting for Open WebUI")
            sys.exit(1)

    # Get token via authentication if not provided directly
    if not token and email and password:
        # Try to authenticate first
        token = get_api_token(url, email, password)

        # If auth failed, try creating the admin user (works if no users exist)
        if not token:
            print("Attempting to create admin user...")
            create_admin_user(url, email, password, name=name)
            # Try authenticating again
            token = get_api_token(url, email, password)

    if not token:
        print("Error: Could not authenticate.")
        print("       Use --env-file .env.neo, or provide --email and --password, or --token")
        sys.exit(1)

    # Load and sync functions
    pipes_dir = Path(args.pipes_dir)
    functions = load_pipe_files(pipes_dir)

    if not functions:
        print(f"No pipe files found in {pipes_dir}")
        sys.exit(0)

    print(f"\nFound {len(functions)} function(s) to sync:")
    for f in functions:
        print(f"  - {f['name']} ({f['id']})")
    print()

    if sync_functions(url, token, functions):
        print("\nAll functions synced successfully!")
    else:
        print("\nSome functions failed to sync")
        sys.exit(1)


if __name__ == "__main__":
    main()
