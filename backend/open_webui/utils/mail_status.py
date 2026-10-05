"""Mail search details retained as translatable status parameters."""

from typing import Any


def mail_search_status(status: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    options = []
    if arguments.get('order') in {'newest', 'relevance'}:
        options.append({'label': 'newest first' if arguments['order'] == 'newest' else 'relevance', 'value': ''})
    for field, label in (('from_addresses', 'From'), ('to_addresses', 'To'), ('cc_addresses', 'Cc')):
        values = arguments.get(field)
        if isinstance(values, list) and all(isinstance(value, str) for value in values) and values:
            options.append({'label': label, 'value': ', '.join(values)})
    description = status.get('description', '')
    if not options or '"{{keywords}}"' not in description:
        return status
    return {
        **status,
        'description': description.replace('"{{keywords}}"', '"{{keywords}}" ({{options}})'),
        'options': '; '.join(
            option['label'] + (': ' + option['value'] if option['value'] else '') for option in options
        ),
        'mail_options': options,
    }
