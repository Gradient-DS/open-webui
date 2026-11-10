"""Measure tenant configuration through its read-only public projection."""

from contextlib import contextmanager
import os

import requests

from .client import json_body

EXPORT = '/api/config'
CONFIG_TIMEOUT_SECONDS = float(os.getenv('ATTACK_CONFIG_TIMEOUT_SECONDS', '300'))
NO_RESPONSE = (requests.exceptions.Timeout, requests.exceptions.ConnectionError)


def snapshot(client):
    response = client.request('GET', EXPORT, timeout=CONFIG_TIMEOUT_SECONDS)
    try:
        body = json_body(response)
        if response.status_code != 200 or not isinstance(body, dict):
            raise RuntimeError(f'Configuration read failed (HTTP {response.status_code})')
        return body
    finally:
        response.close()


def restore(client, before, *, report, route):
    after = snapshot(client)
    if before != after:
        report({'finding': 'configuration_changed', 'route': route})
        raise RuntimeError('A product request changed read-only tenant configuration')


@contextmanager
def preserve_configuration(client, *, report, route, unverified):
    try:
        before = snapshot(client)
    except NO_RESPONSE as error:
        unverified(f'configuration snapshot got no response before the route: {type(error).__name__}: {error}')
        yield
        return
    try:
        yield
    finally:
        try:
            restore(client, before, report=report, route=route)
        except NO_RESPONSE as error:
            unverified(f'configuration snapshot got no response after the route: {type(error).__name__}: {error}')
