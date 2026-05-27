from datetime import datetime
from pydantic import BaseModel, Field


class TraceRunRequest(BaseModel):
    title: str = Field(default="Sin titulo")
    base_sql: str
    reference_sql: str


class TraceRunResponse(BaseModel):
    id: int
    title: str
    base_sql: str
    reference_sql: str
    output_sql: str
    report: dict
    created_at: datetime


class RewritePreviewResponse(BaseModel):
    output_sql: str
    report: dict
