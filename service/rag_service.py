import os
import logging
from typing import List, Optional, Tuple
import aiohttp

from sqlalchemy import select, desc, func, bindparam
from pgvector.sqlalchemy import Vector

from core.di import container
from adapter.db.models import GroupContextDoc, ContextDocument
from adapter.llm.client import LLMClient as LLMService, _get_client
from core.settings import RAG_SYSTEM_PROMPT, RAG_USER_PROMPT_TEMPLATE
from core.settings import EMBEDDING_MODEL as DEFAULT_EMBEDDING_MODEL
from domain.schemas.rag import RAGAnswer, RAGContext
from adapter.processor.firecrawl import fetch_page_summary
from adapter.processor.document_processor import extract_text_from_document


logger = logging.getLogger(__name__)


def _format_context(chunks: List[Tuple[GroupContextDoc, float]]) -> str:
    parts: List[str] = []
    for idx, (chunk, sim) in enumerate(chunks, start=1):
        title = chunk.source_name or "(untitled)"
        parts.append(f"[{idx}] {title} ({sim:.2f})\n{(chunk.content or '').strip()}\n")
    return "\n".join(parts)


class RAGService:
    """Answers users' questions using Retrieval-Augmented Generation over group context.

    Retrieval is performed against `GroupContextDoc.embedding` (pgvector) for a given group.
    Generation uses `LLMService.structured` to produce a validated `RAGAnswer`.
    """

    def __init__(self, model: Optional[str] = None, embedding_model: Optional[str] = None) -> None:
        self.llm = LLMService(model=model)
        self.embedding_model = embedding_model or DEFAULT_EMBEDDING_MODEL

    async def _embed(self, text: str) -> List[float]:
        text = (text or "").strip()
        if not text:
            return []
        client = _get_client()
        resp = await client.embeddings.create(model=self.embedding_model, input=[text])
        return list(resp.data[0].embedding)

    async def _retrieve(
        self,
        group_id: int,
        query_embedding: List[float],
        *,
        k: int = 5,
        threshold: float = 0.4,
    ) -> List[Tuple[GroupContextDoc, float]]:
        if not query_embedding:
            return []
        similarity_expr = (1 - func.cosine_distance(
            GroupContextDoc.embedding,
            bindparam("query_vector", type_=Vector(1536))
        ))
        stmt = (
            select(GroupContextDoc, similarity_expr.label("similarity"))
            .where(GroupContextDoc.group_id == str(group_id))
            .where(similarity_expr > threshold)
            .order_by(desc("similarity"))
            .limit(k)
        )

        async with container.db() as session:
            result = await session.execute(stmt, {"query_vector": query_embedding})
            rows = result.all()
        # rows: List[Tuple[GroupContextDoc, float]]
        return [(row[0], float(row[1])) for row in rows]

    # -----------------
    # Ingest utilities
    # -----------------

    @staticmethod
    def chunk_text(text: str, target_chars: int = 2000) -> List[str]:
        """Split text into roughly target_chars chunks, sentence-aware when possible."""
        text = (text or "").strip()
        if not text:
            return []
        if len(text) <= target_chars:
            return [text]
        parts: List[str] = []
        buf: List[str] = []
        total = 0
        for sent in text.replace("\n", " ").split(". "):
            s = sent if sent.endswith(".") else sent + "."
            if total + len(s) > target_chars and buf:
                parts.append(" ".join(buf).strip())
                buf, total = [], 0
            buf.append(s)
            total += len(s) + 1
        if buf:
            parts.append(" ".join(buf).strip())
        out: List[str] = []
        for p in parts:
            if len(p) <= target_chars:
                out.append(p)
            else:
                for i in range(0, len(p), target_chars):
                    out.append(p[i : i + target_chars])
        return [c for c in out if c]

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Embed a list of texts using the configured embedding model (batched)."""
        if not texts:
            return []
        client = _get_client()
        resp = await client.embeddings.create(model=self.embedding_model, input=texts)
        return [list(d.embedding) for d in resp.data]

    async def insert_chunks_via_sqlalchemy(
        self,
        group_id: int,
        uploader_id: int,
        source_type: str,
        source_name: str,
        chunks: List[str],
        embeddings: List[List[float]],
        original_name: str | None = None,
        url: str | None = None,
    ) -> None:
        if not chunks:
            return
        async with container.db() as session:
            parent = ContextDocument(
                group_id=str(group_id),
                uploader_id=uploader_id,
                source_type=source_type,
                source_name=source_name,
                original_name=original_name or source_name,
                url=url,
                num_chunks=len(chunks),
            )
            session.add(parent)
            await session.flush()

            docs = []
            for c, e in zip(chunks, embeddings):
                docs.append(
                    GroupContextDoc(
                        group_id=str(group_id),
                        document_id=parent.id,
                        uploader_id=uploader_id,
                        source_type=source_type,
                        source_name=source_name,
                        content=c,
                        embedding=e,
                    )
                )
            session.add_all(docs)
            await session.commit()

    async def _download_telegram_file(self, file_id: str, bot_token: str) -> Tuple[bytes, str]:
        api_url = f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}"
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    raise RuntimeError(f"Telegram getFile failed: {data}")
                file_path = data["result"]["file_path"]
            file_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
            async with session.get(file_url) as file_resp:
                file_bytes = await file_resp.read()
        return file_bytes, file_path

    async def process_file_context(self, group_id: int, uploader_id: int, file_id: str, file_name: str | None, bot_token: str) -> None:
        """Ingest a Telegram file (document/photo/audio/video) into group context."""
        content_bytes, fname = await self._download_telegram_file(file_id, bot_token)
        text = extract_text_from_document(content_bytes, file_name or fname)
        if not text:
            return
        chunks = self.chunk_text(text)
        vectors = await self.embed_texts(chunks)
        await self.insert_chunks_via_sqlalchemy(
            group_id,
            uploader_id,
            source_type="file",
            source_name=(file_name or fname),
            chunks=chunks,
            embeddings=vectors,
            original_name=file_name or fname,
        )

    async def process_link_context(self, group_id: int, uploader_id: int, url: str) -> None:
        """Crawl a link and ingest the content into group context."""
        raw_text = fetch_page_summary(url, return_markdown=True)
        text = raw_text or ""
        if not text:
            return
        chunks = self.chunk_text(text)
        vectors = await self.embed_texts(chunks)
        await self.insert_chunks_via_sqlalchemy(
            group_id,
            uploader_id,
            source_type="link",
            source_name=url,
            chunks=chunks,
            embeddings=vectors,
            url=url,
        )

    async def process_text_context(self, group_id: int, uploader_id: int, text: str, source_name: str = "text") -> None:
        """Ingest plain text into group context."""
        text = (text or "").strip()
        if not text:
            return
        chunks = self.chunk_text(text)
        vectors = await self.embed_texts(chunks)
        await self.insert_chunks_via_sqlalchemy(
            group_id,
            uploader_id,
            source_type="text",
            source_name=source_name,
            chunks=chunks,
            embeddings=vectors,
        )

    async def answer(
        self,
        *,
        group_id: int,
        question: str,
        k: int = 5,
        threshold: float = 0.4,
        temperature: float = 0.0,
        max_tokens: int = 400,
    ) -> Optional[RAGAnswer]:
        try:
            query_vec = await self._embed(question)
        except Exception as e:
            logger.error(f"[RAGService] Embedding failed: {e}")
            return None

        try:
            top_chunks = await self._retrieve(group_id, query_vec, k=k, threshold=threshold)
        except Exception as e:
            logger.error(f"[RAGService] Retrieval failed: {e}")
            top_chunks = []

        context_text = _format_context(top_chunks)
        user_prompt = RAG_USER_PROMPT_TEMPLATE.format(question=question, context=context_text)

        # Prepare fallback used_context and confidence
        used_context_items: List[RAGContext] = [
            RAGContext(
                document_id=str(chunk.document_id),
                title=chunk.source_name,
                similarity=sim,
                chunk_text=chunk.content or "",
            )
            for chunk, sim in top_chunks
        ]
        avg_conf = 0.0
        if top_chunks:
            avg_conf = sum(sim for _, sim in top_chunks) / float(len(top_chunks))

        result = await self.llm.structured(
            prompt=user_prompt,
            model_cls=RAGAnswer,
            temperature=temperature,
            max_tokens=max_tokens,
            system=RAG_SYSTEM_PROMPT,
            extra_instructions=(
                "Populate used_context with the passages you relied on. "
                "Set confidence to a value between 0 and 1 reflecting how well the context supports the answer."
            ),
        )

        if result:
            # If model returned an answer but omitted used_context, fill it.
            if not result.used_context and used_context_items:
                result.used_context = used_context_items
            # If model omitted confidence, fill with average similarity.
            if result.confidence is None:
                result.confidence = max(0.0, min(1.0, avg_conf))
            return result

        # Fallback conservative response
        fallback_answer = "I’m not sure — that might be outside the group’s shared knowledge."
        return RAGAnswer(
            question=question,
            answer=fallback_answer,
            confidence=max(0.0, min(1.0, avg_conf)),
            used_context=used_context_items,
            rationale=None,
        )
