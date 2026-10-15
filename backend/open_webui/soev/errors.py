"""Safe messages for platform problems; upstream details may name private files."""

from open_webui.soev.client import SoevApiError

_MESSAGES = {
    ('scope_insufficient', 'collection:access_owner'): (
        'Only the owner can change access to this knowledge base.',
        'Alleen de eigenaar kan de toegang tot deze kennisbank wijzigen.',
    ),
    ('policy_forbids', 'synced_collection:not_shareable'): (
        'Synced knowledge bases cannot be shared.',
        'Gesynchroniseerde kennisbanken kunnen niet worden gedeeld.',
    ),
    ('invalid_field', 'principals:max_64'): (
        'You can share with up to 64 users or groups. Use a group or everyone for wider access.',
        'Je kunt delen met maximaal 64 gebruikers of groepen. Gebruik een groep of iedereen voor ruimere toegang.',
    ),
}
_GENERIC = (
    'The platform could not complete this request.',
    'Het platform kon dit verzoek niet uitvoeren.',
)


def problem_message(error: SoevApiError, language: str) -> str:
    """Select a fixed English or Dutch message without reading the problem detail."""
    messages = _MESSAGES.get((error.code, error.constraint), _GENERIC)
    return messages[1 if language.lower().startswith('nl') else 0]
