"""Durable worker cursors and idempotency receipts, separate from product settings."""

from open_webui.internal.db import Base
from sqlalchemy import JSON, Column, Text


class RuntimeState(Base):
    __tablename__ = 'runtime_state'

    key = Column(Text, primary_key=True)
    value = Column(JSON, nullable=False)
