"""TTL resolution logic for the data retention system."""

import time


def get_effective_ttl_days(
    master_ttl: int,
    entity_ttl: int,
) -> int:
    """Resolve effective TTL for an entity type.

    Args:
        master_ttl: DATA_RETENTION_TTL_DAYS (0 = system disabled)
        entity_ttl: per-entity override (0 = inherit master)

    Returns:
        Effective TTL in days. 0 means disabled (no cleanup).
    """
    if master_ttl <= 0:
        return 0  # System disabled
    if entity_ttl > 0:
        return entity_ttl  # Entity-specific override
    return master_ttl  # Inherit from master


def get_cutoff_timestamp(ttl_days: int) -> int:
    """Convert TTL days to a cutoff epoch timestamp.

    Returns the epoch timestamp before which data is considered stale.
    """
    return int(time.time()) - (ttl_days * 86400)


def is_retention_enabled(master_ttl: int) -> bool:
    """Check if the retention system is enabled."""
    return master_ttl > 0
