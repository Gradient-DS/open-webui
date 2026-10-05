"""[Gradient] State of the v2 migration: config snapshot, rewritten model ids and step markers."""

from open_webui.internal.db import Base
from sqlalchemy import BigInteger, Column, Text


class ConfigBackup(Base):
    """A config row as it was before the switch; value is the raw JSON text, NULL when no row existed."""

    __tablename__ = 'config_backup'

    migration_id = Column(Text, primary_key=True)
    key = Column(Text, primary_key=True)
    value = Column(Text, nullable=True)
    updated_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)


class ModelIdBackup(Base):
    """One rewritten model reference; path is a JSON list of keys inside the row's field."""

    __tablename__ = 'model_id_backup'

    migration_id = Column(Text, primary_key=True)
    site = Column(Text, primary_key=True)
    row_id = Column(Text, primary_key=True)
    path = Column(Text, primary_key=True)
    old_value = Column(Text, nullable=False)
    new_value = Column(Text, nullable=False)
    created_at = Column(BigInteger, nullable=False)


class MigrationMarker(Base):
    """A once-per-migration step that has completed."""

    __tablename__ = 'migration_marker'

    migration_id = Column(Text, primary_key=True)
    step = Column(Text, primary_key=True)
    created_at = Column(BigInteger, nullable=False)
