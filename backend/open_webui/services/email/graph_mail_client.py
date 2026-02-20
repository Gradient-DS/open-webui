import re

import httpx

from open_webui.services.email.auth import get_mail_access_token

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


async def send_mail(
    app,
    to_address: str,
    subject: str,
    html_body: str,
) -> bool:
    """Send email via Microsoft Graph API. Returns True on success."""
    token = await get_mail_access_token(app)
    from_address = str(app.state.config.EMAIL_FROM_ADDRESS)

    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": [{"emailAddress": {"address": to_address}}],
        },
        "saveToSentItems": False,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{GRAPH_BASE_URL}/users/{from_address}/sendMail",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
        )

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 60))
            raise Exception(f"Rate limited. Retry after {retry_after}s")

        resp.raise_for_status()
        return True


APP_NAME = "soev.ai"
# Word Joiner (U+2060) before the dot breaks domain pattern detection in email clients.
# Unlike ZWJ, HTML comments, or <a> without href, this survives Gmail/Outlook sanitization.
APP_NAME_HTML = "soev&#x2060;.ai"

_STRINGS = {
    "en": {
        "subject_with_client": f"You've been invited to the {APP_NAME} environment of {{client_name}}",
        "subject": f"You've been invited to {APP_NAME}",
        "heading_with_client": f"You've been invited to the {APP_NAME_HTML} environment of {{client_name}}",
        "heading": f"You've been invited to {APP_NAME_HTML}",
        "body": "{invited_by_name} has invited you to join. Click the button below to create your account.",
        "button": "Accept Invite",
        "footer": "This invite expires in {expiry_days} days. If you didn't expect this email, you can safely ignore it.",
    },
    "nl": {
        "subject_with_client": f"Je bent uitgenodigd voor de {APP_NAME}-omgeving van {{client_name}}",
        "subject": f"Je bent uitgenodigd voor {APP_NAME}",
        "heading_with_client": f"Je bent uitgenodigd voor de {APP_NAME_HTML}-omgeving van {{client_name}}",
        "heading": f"Je bent uitgenodigd voor {APP_NAME_HTML}",
        "body": "{invited_by_name} heeft je uitgenodigd. Klik op de onderstaande knop om je account aan te maken.",
        "button": "Uitnodiging accepteren",
        "footer": "Deze uitnodiging verloopt over {expiry_days} dagen. Als je deze e-mail niet verwachtte, kun je deze veilig negeren.",
    },
}


def _get_strings(locale: str) -> dict:
    lang = locale.split("-")[0].lower() if locale else "en"
    return _STRINGS.get(lang, _STRINGS["en"])


def _prevent_email_autolink(text: str) -> str:
    """Insert Word Joiner (U+2060) before dots in domain-like patterns (e.g. soev.ai)
    to prevent email clients from auto-linking them. The invisible character breaks
    pattern detection in Gmail and Outlook without affecting visual rendering."""
    return re.sub(r"(?<=\w)\.(?=[a-zA-Z]{2,}\b)", "&#x2060;.", text)


def render_invite_subject(
    locale: str = "en",
    client_name: str = "",
    custom_subject: str = "",
) -> str:
    if custom_subject:
        return custom_subject
    strings = _get_strings(locale)
    if client_name:
        return strings["subject_with_client"].format(client_name=client_name)
    return strings["subject"]


def render_invite_email(
    invite_url: str,
    invited_by_name: str,
    locale: str = "en",
    expiry_hours: int = 168,
    client_name: str = "",
    custom_heading: str = "",
) -> str:
    strings = _get_strings(locale)
    expiry_days = max(1, expiry_hours // 24)

    if custom_heading:
        heading = _prevent_email_autolink(custom_heading)
    elif client_name:
        heading = strings["heading_with_client"].format(client_name=client_name)
        heading = _prevent_email_autolink(heading)
    else:
        heading = strings["heading"]
    body = strings["body"].format(invited_by_name=invited_by_name)
    button = strings["button"]
    footer = strings["footer"].format(expiry_days=expiry_days)

    return f"""\
<!DOCTYPE html>
<html>
<head>
<meta name="format-detection" content="telephone=no, date=no, address=no, email=no, url=no">
<style type="text/css">
u + #body a {{
    color: inherit !important;
    text-decoration: none !important;
    font-size: inherit !important;
    font-weight: inherit !important;
}}
</style>
</head>
<body id="body">
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            max-width: 560px; margin: 0 auto; padding: 40px 20px;">
    <h2 style="color: #1a1a1a; margin-bottom: 8px;">
        {heading}
    </h2>
    <p style="color: #4a4a4a; font-size: 16px; line-height: 1.5;">
        {body}
    </p>
    <a href="{invite_url}"
       style="display: inline-block; background: #0f172a; color: #ffffff;
              padding: 12px 24px; border-radius: 8px; text-decoration: none;
              font-weight: 500; margin: 24px 0;">
        <span style="color: #ffffff;">{button}</span>
    </a>
    <p style="color: #9a9a9a; font-size: 13px; margin-top: 32px;">
        {footer}
    </p>
</div>
</body>
</html>"""
