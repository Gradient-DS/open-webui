#!/usr/bin/env python3
"""
Register the NEO NL Document Assistant Pipe via Open WebUI API.

Usage:
    python scripts/pipes/register_neo_nl_pipe.py --url http://localhost:8080 --token YOUR_API_TOKEN

To get an API token:
    1. Go to Open WebUI Settings -> Account
    2. Generate an API key
"""

import argparse
import requests
import sys
from pathlib import Path


def get_pipe_content() -> str:
    """Read the Pipe code from the reference file."""
    pipe_file = Path(__file__).parent / "neo_nl_assistant.py"
    return pipe_file.read_text()


def register_function(base_url: str, token: str, update: bool = False) -> dict:
    """Register or update the NEO NL Assistant function via API."""

    content = get_pipe_content()

    payload = {
        "id": "neo_nl_assistant",
        "name": "NEO NL Document Assistant",
        "content": content,
        "meta": {
            "description": "Search nuclear safety documents via MCP and generate responses with citations"
        }
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    # Try to create first
    url = f"{base_url.rstrip('/')}/api/v1/functions/create"
    response = requests.post(url, json=payload, headers=headers)

    if response.status_code == 400 and "taken" in response.text.lower():
        if update:
            # Function exists, update it
            url = f"{base_url.rstrip('/')}/api/v1/functions/id/neo_nl_assistant/update"
            response = requests.post(url, json=payload, headers=headers)
        else:
            print("Function already exists. Use --update to update it.")
            sys.exit(1)

    response.raise_for_status()
    return response.json()


def toggle_active(base_url: str, token: str) -> dict:
    """Toggle the function to active state."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    # First check current state
    url = f"{base_url.rstrip('/')}/api/v1/functions/id/neo_nl_assistant"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    func = response.json()

    if not func.get("is_active"):
        # Toggle to active
        url = f"{base_url.rstrip('/')}/api/v1/functions/id/neo_nl_assistant/toggle"
        response = requests.post(url, headers=headers)
        response.raise_for_status()
        return response.json()

    return func


def main():
    parser = argparse.ArgumentParser(description="Register NEO NL Pipe in Open WebUI")
    parser.add_argument("--url", default="http://localhost:8080", help="Open WebUI base URL")
    parser.add_argument("--token", required=True, help="API token (from Settings -> Account)")
    parser.add_argument("--update", action="store_true", help="Update if function already exists")

    args = parser.parse_args()

    print(f"Registering NEO NL Document Assistant at {args.url}...")

    try:
        result = register_function(args.url, args.token, args.update)
        print(f"Function registered successfully!")
        print(f"  ID: {result.get('id')}")
        print(f"  Name: {result.get('name')}")
        print(f"  Type: {result.get('type')}")
        print(f"  Active: {result.get('is_active')}")

        # Activate the function
        if not result.get("is_active"):
            print("\nActivating function...")
            result = toggle_active(args.url, args.token)
            print(f"  Active: {result.get('is_active')}")

        print("\nDone! The NEO NL Document Assistant should now appear in the model selector.")

    except requests.exceptions.HTTPError as e:
        print(f"Error: {e}")
        print(f"Response: {e.response.text if hasattr(e, 'response') else 'N/A'}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
