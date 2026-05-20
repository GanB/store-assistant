from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StoreCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    phone: str = Field(min_length=1)


class StoreRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    phone_e164: str
    thread_id: str | None = None
    created_at: datetime


class SummaryCreate(BaseModel):
    summary_text: str = Field(min_length=1)
    conversation_metadata: dict[str, Any] | None = None
    thread_id: str | None = None


class SummaryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    thread_id: str | None = None
    summary_text: str
    created_at: datetime
