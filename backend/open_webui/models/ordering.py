"""ORDER BY for a column a request names, without trusting the name."""

from open_webui.internal.db import JSONField
from sqlalchemy import JSON


def request_order(model, order_by, direction, default):
    """The ORDER BY clauses for a query string's `order_by`/`direction`, else `default`.

    Only a real column of `model` qualifies, and not a JSON one: getattr also
    returns relationships and methods, and PostgreSQL has no ordering for json.
    An unknown column or direction keeps `default` rather than raising, which
    answered 500 on the chat lists (PLANE-008).
    """
    column = model.__table__.columns.get(order_by or '')
    direction = (direction or '').lower()
    if column is None or isinstance(column.type, (JSON, JSONField)) or direction not in ('asc', 'desc'):
        return default
    return (column.asc() if direction == 'asc' else column.desc(),)
