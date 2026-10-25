"""Canonical platform references used by the local directory mirror."""

from uuid import UUID


def is_canonical_uuid(value: str) -> bool:
    try:
        return str(UUID(value)) == value
    except (ValueError, AttributeError, TypeError):
        return False


def is_canonical_person_ref(value: str) -> bool:
    return isinstance(value, str) and value.startswith('platform:user:') and is_canonical_uuid(value[14:])


# Portable to PostgreSQL and SQLite; disallow alternate UUID spellings in storage.
_uuid_hex = "replace(substr(person_id, 15), '-', '')"
for _digit in '0123456789abcdef':
    _uuid_hex = f"replace({_uuid_hex}, '{_digit}', '')"
PERSON_REF_CHECK = (
    'person_id IS NULL OR (length(person_id) = 50 '
    "AND substr(person_id, 1, 14) = 'platform:user:' "
    "AND substr(person_id, 23, 1) = '-' AND substr(person_id, 28, 1) = '-' "
    "AND substr(person_id, 33, 1) = '-' AND substr(person_id, 38, 1) = '-' "
    f"AND length(replace(substr(person_id, 15), '-', '')) = 32 AND length({_uuid_hex}) = 0)"
)
