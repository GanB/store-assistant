from __future__ import annotations

from pydantic import BaseModel


class SaveStoreResult(BaseModel):
    success: bool
    message: str
    normalized_phone: str | None = None


class GetStorePhoneResult(BaseModel):
    success: bool
    message: str
    phone_e164: str | None = None
    reason: str | None = None
