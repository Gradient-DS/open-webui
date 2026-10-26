"""Platform principals used by resource archival."""

from open_webui import config

ARCHIVE_OWNER = 'platform:svc:archive-owui'


def archive_group_ref() -> str:
    ref = config.SOEV_ARCHIVE_READERS_GROUP_REF
    if not ref.startswith('platform:group:') or not ref.removeprefix('platform:group:'):
        raise ValueError('SOEV_ARCHIVE_READERS_GROUP_REF must be a seeded platform group ref')
    return ref
