from sqlalchemy import Column, DateTime, Integer, Text, func

from .db import Base


class TraceRun(Base):
    __tablename__ = "trace_runs"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(Text, nullable=False, default="Sin titulo")
    base_sql = Column(Text, nullable=False)
    reference_sql = Column(Text, nullable=False)
    output_sql = Column(Text, nullable=False)
    report_json = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
