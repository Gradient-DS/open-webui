"""Product-feedback service: enrichment, structured logging, and Slack delivery.

This is the I/O boundary for the feedback-reporting feature. The router
(routers/feedback_report.py) stays a thin HTTP layer; everything that touches
the outside world — the log line that Loki ingests and the Slack card — lives
here. Chat content never enters a feedback event: the router's context
allowlist is the structural guarantee, and this module only ever reads the
allowlisted keys.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

import aiohttp
from opentelemetry import trace

from open_webui.config import TENANT_NAME
from open_webui.env import CLIENT_NAME, VERSION, WEBUI_BUILD_HASH

log = logging.getLogger(__name__)

CATEGORY_EMOJI = {
    'bug': ':beetle:',
    'idea': ':bulb:',
    'question': ':question:',
    'error': ':rotating_light:',
    'other': ':speech_balloon:',
}


def get_current_trace_id() -> str | None:
    """Current OTel trace id as a hex string, or None when tracing is inactive."""
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        return trace.format_trace_id(ctx.trace_id)
    return None


def build_http_error_body(detail: Any) -> dict:
    """HTTP error-response body with the current OTel trace id attached when active.

    Used by the app-wide HTTPException handler so pre-flight chat HTTP errors
    carry a trace id the frontend can fold into a feedback report.
    """
    body: dict = {'detail': detail}
    trace_id = get_current_trace_id()
    if trace_id:
        body['trace_id'] = trace_id
    return body


def build_feedback_event(
    *,
    category: str,
    description: str,
    context: dict,
    user: Any,
    include_identity: bool,
) -> dict:
    """Enrich a submission into the canonical feedback event. Never includes chat content."""
    event = {
        'event_type': 'user_feedback',
        'ts': datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
        'tenant': TENANT_NAME or 'unknown',
        'client_name': CLIENT_NAME or '',
        'category': category,
        'description': description,
        'app_version': VERSION,
        'build_hash': WEBUI_BUILD_HASH,
        # client-reported, allowlisted context only — see FeedbackReportContext
        'route': context.get('route'),
        'user_agent': context.get('user_agent'),
        'error_message': context.get('error_message'),
        'error_detail': context.get('error_detail'),
        'model': context.get('model'),
        'chat_id': context.get('chat_id'),  # identifier only — never chat content
        'trace_id': context.get('trace_id'),
    }
    if include_identity and user is not None:
        event['user'] = {'id': user.id, 'email': user.email, 'name': user.name}
    return event


def emit_feedback_log(event: dict) -> None:
    """Emit the event as one self-contained JSON line on stdout (ingested by Loki)."""
    log.info(json.dumps(event, ensure_ascii=False, default=str))


async def post_feedback_to_slack(event: dict, webhook_url: str, trace_url_template: str = '') -> bool:
    """Post a Block Kit card. Best-effort: returns False on any failure, never raises."""
    if not webhook_url:
        return False
    try:
        blocks = _build_slack_blocks(event, trace_url_template)
        async with aiohttp.ClientSession(trust_env=True, timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.post(webhook_url, json={'blocks': blocks}) as resp:
                return resp.status == 200
    except Exception as e:
        log.warning(f'feedback: Slack post failed: {e}')
        return False


def _build_slack_blocks(event: dict, trace_url_template: str) -> list[dict]:
    emoji = CATEGORY_EMOJI.get(event['category'], ':speech_balloon:')
    tenant_label = event.get('client_name') or event.get('tenant')
    blocks: list[dict] = [
        {
            'type': 'header',
            'text': {'type': 'plain_text', 'text': f'{emoji} New feedback · {event["category"]}'},
        },
        {'type': 'section', 'text': {'type': 'mrkdwn', 'text': event['description'][:2900]}},
    ]
    ctx_parts = [f'*Tenant:* {tenant_label}', f'*Version:* {event["app_version"]}']
    if event.get('route'):
        ctx_parts.append(f'*Route:* `{event["route"]}`')
    if event.get('user'):
        ctx_parts.append(f'*User:* {event["user"]["email"]}')
    blocks.append({'type': 'context', 'elements': [{'type': 'mrkdwn', 'text': '  ·  '.join(ctx_parts)}]})
    if event.get('error_message'):
        blocks.append(
            {
                'type': 'section',
                'text': {'type': 'mrkdwn', 'text': f'*Error:* ```{str(event["error_message"])[:500]}```'},
            }
        )
    if event.get('trace_id') and trace_url_template:
        url = trace_url_template.replace('{trace_id}', event['trace_id'])
        blocks.append(
            {
                'type': 'actions',
                'elements': [
                    {'type': 'button', 'text': {'type': 'plain_text', 'text': 'View trace in Tempo'}, 'url': url}
                ],
            }
        )
    return blocks
