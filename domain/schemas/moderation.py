from pydantic import BaseModel, Field
from typing import List, Optional

class SpamVerdict(BaseModel):
    spam: bool = Field(..., description="Whether the message is spam or not")
    reason: str = Field(..., description="The reason for the spam verdict")
    confidence: float = Field(..., description="The confidence in the spam verdict")
    categories: List[str] = Field(..., description="The categories of the spam verdict")