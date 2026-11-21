"""Legacy warning rows retained solely for console offboarding erasure."""

from sqlalchemy import BigInteger, Column, JSON, String, Text
from open_webui.internal.db import Base


class DataWarningLog(Base):
    __tablename__ = 'data_warning_log'

    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    chat_id = Column(String, nullable=False, index=True)
    model_id = Column(String, nullable=False)
    capabilities = Column(JSON, nullable=False)  # list of capability strings
    warning_message = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)
