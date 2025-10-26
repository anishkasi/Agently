from pydantic import BaseModel, Field
from typing import List, Optional

class RAGContext(BaseModel):
    document_id: str
    title: Optional[str] = None
    similarity: float
    chunk_text: str

class RAGAnswer(BaseModel):
    question: str
    answer: str
    confidence: float = Field(..., ge=0, le=1)
    used_context: List[RAGContext]
    rationale: Optional[str] = None