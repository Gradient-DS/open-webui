"""The console owns group identity and membership."""

from typing import NoReturn

from fastapi import HTTPException


def refuse_group_write() -> NoReturn:
    raise HTTPException(
        403,
        detail={
            'code': 'platform_groups_read_only',
            'detail': 'Groups are managed in the platform console.',
        },
    )
