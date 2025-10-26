from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, model_validator


class Intent(str, Enum):
    qna = "qna"
    chat = "chat"
    command = "command"
    moderation = "moderation"
    other = "other"


class Evidence(BaseModel):
    rationale: str = Field(..., description="Short why the model chose this intent.")
    cues: List[str] = Field(default_factory=list, description="Trigger words/patterns seen.")
    recent_refs: List[str] = Field(default_factory=list, description="Recent message ids or excerpts used.")


class RouterOutput(BaseModel):
    intent: Intent
    confidence: float = Field(..., ge=0, le=1)
    is_group_qna_eligible: bool = False
    evidence: Evidence

    @model_validator(mode="after")
    def check_qna_guard(self):
        if self.intent == Intent.qna and not bool(self.is_group_qna_eligible):
            self.intent = Intent.other
        return self


