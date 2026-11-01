"""Canonical platform references used by the local directory mirror."""

from uuid import UUID


def is_canonical_uuid(value: str) -> bool:
    try:
        return str(UUID(value)) == value
    except (ValueError, AttributeError, TypeError):
        return False


def is_canonical_person_ref(value: str) -> bool:
    return isinstance(value, str) and value.startswith('platform:user:') and is_canonical_uuid(value[14:])


async def person_ref(user_id: str) -> str:
    from open_webui.models.users import Users

    user = await Users.get_user_by_id(user_id)
    ref = user.person_id if user is not None else None
    if not is_canonical_person_ref(ref):
        raise ValueError(f'User {user_id!r} has no valid platform person binding (users.person_id).')
    return ref


async def collection_access(body: dict) -> dict:
    refs = set(body.get('principals', [])) | set(body.get('writers', []))
    people = {ref: await person_ref(ref.removeprefix('owui:user:')) for ref in refs if ref.startswith('owui:user:')}
    return {
        **body,
        **{field: sorted({people.get(ref, ref) for ref in body[field]}) for field in ('principals', 'writers')},
    }


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
