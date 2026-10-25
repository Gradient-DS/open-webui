"""Safe messages for platform problems; upstream details may name private files."""

from open_webui.soev.client import SoevApiError

_MESSAGES = {
    ('policy_forbids', 'offboarding:in_progress'): (
        'Resume the existing offboarding operation before changing its mode or target.',
        'Hervat de bestaande uitdiensttreding voordat je de modus of het overdrachtsdoel wijzigt.',
    ),
    ('invalid_field', 'offboarding:target'): (
        'Choose another approved user as the transfer target.',
        'Kies een andere goedgekeurde gebruiker als overdrachtsdoel.',
    ),
    ('policy_forbids', 'offboarding:archive_disabled'): (
        'User archival is not enabled.',
        'Het archiveren van gebruikers is niet ingeschakeld.',
    ),
    ('scope_insufficient', 'identity:platform_login'): (
        'Sign in with your organisation account to use this feature.',
        'Log in met je organisatieaccount om deze functie te gebruiken.',
    ),
    ('scope_insufficient', 'identity:eligible_role'): (
        'Only approved users can connect to the platform.',
        'Alleen goedgekeurde gebruikers kunnen verbinding maken met het platform.',
    ),
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


def shared_deletion_message(language: str) -> str:
    """Describe the confirmation requirement without exposing private item names."""
    messages = (
        'Confirm deletion of every shared item before deleting this user.',
        'Bevestig de verwijdering van elk gedeeld item voordat je deze gebruiker verwijdert.',
    )
    return messages[1 if language.lower().startswith('nl') else 0]
