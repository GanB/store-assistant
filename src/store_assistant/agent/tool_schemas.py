from __future__ import annotations

from pydantic import BaseModel, Field


class SaveStoreInput(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=255,
        description=(
            "Display name of the store. Must be unique across all stored records. "
            "Pass through what the user provided; do not paraphrase."
        ),
    )
    phone: str = Field(
        min_length=1,
        description=(
            "Phone number of the store. Accepts US-formatted variants "
            "(e.g. '919-555-0100', '(919) 555-0100') or international E.164 "
            "(e.g. '+441632960000'). The tool will validate and normalize it to E.164."
        ),
    )


class GetStorePhoneInput(BaseModel):
    name: str = Field(
        min_length=1,
        description="Display name of the store to look up.",
    )
    passphrase: str = Field(
        min_length=1,
        description=(
            "Passphrase the user has provided to authorize retrieval. "
            "Pass through verbatim what the user typed; do not invent, guess, or modify it."
        ),
    )
