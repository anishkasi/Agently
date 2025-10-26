# Adapter-local models: copied from db/models.py to avoid shims
from sqlalchemy import (
    Column, Integer, BigInteger, String, Boolean, Float, Text,
    DateTime, ForeignKey, JSON, func, UniqueConstraint, Index, CheckConstraint
)
from sqlalchemy.orm import relationship
from adapter.db.session import Base
import uuid
from sqlalchemy.dialects.postgresql import UUID
from pgvector.sqlalchemy import Vector
from sqlalchemy import select, literal_column, desc


class Group(Base):
    __tablename__ = "groups"

    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(BigInteger, unique=True, index=True)
    name = Column(String(255))
    has_config = Column(Boolean, default=False)
    language = Column(String(10), default="en")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        # Added index on name for fuzzy lookups
        Index("idx_groups_name", "name"),
    )

    # Relationships
    users = relationship("GroupUser", back_populates="group")
    messages = relationship("Message", back_populates="group")
    moderation_actions = relationship("ModerationAction", back_populates="group")
    documents = relationship("Document", back_populates="group")
    bot_config = relationship("BotConfig", back_populates="group", uselist=False)

# -------------------
# Users
# -------------------
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, unique=True, index=True)
    username = Column(String(255))
    reputation_score = Column(Float, default=0.0)
    first_seen = Column(DateTime(timezone=True), server_default=func.now())
    last_seen = Column(DateTime(timezone=True), server_default=func.now())
    is_banned_global = Column(Boolean, default=False)
    is_bot = Column(Boolean, default=False)

    __table_args__ = (
        # Added composite index for quick resolution by user_id and username
        Index("idx_users_userid_username", "user_id", "username"),
        # Added index on reputation_score for analytics/moderation filtering
        Index("idx_users_reputation_score", "reputation_score"),
    )

    groups = relationship("GroupUser", back_populates="user")
    messages = relationship("Message", back_populates="user")
    moderation_actions = relationship("ModerationAction", back_populates="user")

# -------------------
# Association table (many-to-many)
# -------------------
class GroupUser(Base):
    __tablename__ = "group_users"
    # Use external identifiers for FK: groups.chat_id and users.user_id
    group_id = Column(BigInteger, ForeignKey("groups.chat_id"), primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.user_id"), primary_key=True)
    joined_at = Column(DateTime(timezone=True), server_default=func.now())
    role = Column(String(50), default="member")  # "admin", "member", "banned", "left"
    is_active = Column(Boolean, default=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    __table_args__ = (
        Index("idx_group_users_updated_at", "updated_at"),
        Index("idx_group_users_role", "role"),
        Index("idx_group_users_is_active", "is_active"),
    )

    group = relationship("Group", back_populates="users")
    user = relationship("User", back_populates="groups")

# -------------------
# Messages (Multimodal Log)
# -------------------
class Message(Base):
    __tablename__ = "messages"

    id = Column(BigInteger, primary_key=True, index=True)
    # Use external identifiers for FKs to align with GroupUser
    group_id = Column(BigInteger, ForeignKey("groups.chat_id"))
    user_id = Column(BigInteger, ForeignKey("users.user_id"))
    message_type = Column(String(50), default="text", index=True)  # text, image, video, audio, document, link
    content = Column(Text)  # message text or link url
    caption = Column(Text)  # for media captions
    summary = Column(Text)
    reply_to_id = Column(BigInteger, ForeignKey("messages.id"), nullable=True)
    is_spam = Column(Boolean, default=False, index=True)
    route_tag = Column(String(50))  # e.g., 'support_agent', 'sales_agent'
    meta = Column(JSON)
    processed = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_messages_groupid_createdat", "group_id", "created_at"),
        Index("idx_messages_userid_createdat", "user_id", "created_at"),
        Index("idx_messages_is_spam", "is_spam"),
        Index("idx_messages_processed", "processed"),
    )

    group = relationship("Group", back_populates="messages", foreign_keys=[group_id])
    user = relationship("User", back_populates="messages", foreign_keys=[user_id])
    media_assets = relationship("MediaAsset", back_populates="message", cascade="all, delete-orphan")
    links = relationship("Link", back_populates="message", cascade="all, delete-orphan")
    reply_to = relationship("Message", remote_side=[id], backref="replies")
    moderation_actions = relationship("ModerationAction", back_populates="message")

# -------------------
# Media Assets
# -------------------
class MediaAsset(Base):
    __tablename__ = "media_assets"

    id = Column(BigInteger, primary_key=True, index=True)
    message_id = Column(BigInteger, ForeignKey("messages.id", ondelete="CASCADE"))
    media_type = Column(String(50), index=True)  # image, video, audio, document
    url = Column(Text)
    file_size = Column(BigInteger)
    mime_type = Column(String(100))
    duration = Column(Float)
    width = Column(Integer)
    height = Column(Integer)
    transcription = Column(Text)
    ocr_text = Column(Text)
    summary = Column(Text)
    meta = Column(JSON)
    processed = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_mediaassets_messageid", "message_id"),
        Index("idx_mediaassets_mediatype", "media_type"),
        Index("idx_mediaassets_createdat", "created_at"),
        Index("idx_mediaassets_processed", "processed"),
    )

    message = relationship("Message", back_populates="media_assets")


# -------------------
# Links
# -------------------
class Link(Base):
    __tablename__ = "links"

    id = Column(BigInteger, primary_key=True, index=True)
    message_id = Column(BigInteger, ForeignKey("messages.id", ondelete="CASCADE"))
    url = Column(Text, index=True)
    domain = Column(String(255), index=True)
    is_spam = Column(Boolean, default=False, index=True)
    meta_data = Column(JSON)
    summary = Column(Text)
    processed = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_links_messageid", "message_id"),
        Index("idx_links_domain", "domain"),
        Index("idx_links_is_spam", "is_spam"),
        Index("idx_links_processed", "processed"),
    )

    message = relationship("Message", back_populates="links")

# -------------------
# Moderation Actions
# -------------------
class ModerationAction(Base):
    __tablename__ = "moderationactions"

    id = Column(BigInteger, primary_key=True, index=True)
    message_id = Column(BigInteger, ForeignKey("messages.id"))
    group_id = Column(Integer, ForeignKey("groups.id"))
    user_id = Column(Integer, ForeignKey("users.id"))
    action_type = Column(String(50))
    reason = Column(Text)
    confidence = Column(Float)
    model_used = Column(String(100))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # Added indexes for reports and dashboards filtering by group and user with created_at
        Index("idx_moderationactions_groupid_createdat", "group_id", "created_at"),
        Index("idx_moderationactions_userid_createdat", "user_id", "created_at"),
    )

    group = relationship("Group", back_populates="moderation_actions")
    user = relationship("User", back_populates="moderation_actions")
    message = relationship("Message", back_populates="moderation_actions")

# -------------------
# Spam Results (detailed verdicts)
# -------------------
class SpamResult(Base):
    __tablename__ = "spamresults"

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(BigInteger, ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    spam = Column(Boolean, default=False, index=True)
    confidence = Column(Float)
    category = Column(String(100))
    reason = Column(Text)
    # Treatment outcome fields
    treatment_action = Column(String(50))  # warning_mild | warning_strong | probation | ban | none
    treatment_message = Column(Text)
    deleted = Column(Boolean, default=False)
    points_docked = Column(Integer, default=0)
    final_reputation = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_spamresults_messageid", "message_id"),
        Index("idx_spamresults_createdat", "created_at"),
    )


# -------------------
# Router Results (intent routing logs)
# -------------------
class RouterResult(Base):
    __tablename__ = "routerresults"

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(BigInteger, ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    intent = Column(String(50), index=True)
    confidence = Column(Float)
    is_group_qna_eligible = Column(Boolean, default=False)
    rationale = Column(Text)
    cues = Column(JSON)
    recent_refs = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_routerresults_messageid", "message_id"),
        Index("idx_routerresults_intent", "intent"),
        Index("idx_routerresults_createdat", "created_at"),
    )

# -------------------
# Documents
# -------------------
class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey("groups.id"))
    filename = Column(String(255))
    storage_path = Column(Text)
    vector_store_id = Column(String(255))
    uploaded_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # Added indexes for filtering by group and uploader
        Index("idx_documents_group_id", "group_id"),
        Index("idx_documents_uploaded_by", "uploaded_by"),
    )

    group = relationship("Group", back_populates="documents")

# -------------------
# Bot Configuration
# -------------------
class BotConfig(Base):
    __tablename__ = "botconfig"

    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey("groups.id"), unique=True)
    group_description = Column(Text, default="")
    spam_sensitivity = Column(String(20), default="medium")
    spam_confidence_threshold = Column(Float, default=0.7)
    spam_rules = Column(Text, default="")
    rag_enabled = Column(Boolean, default=True)
    personality = Column(String(50), default="neutral")
    moderation_features = Column(
        JSON,
        default={
            "spam_detection": True,
            "harmful_intent": False,
            "fud_filtering": True,
            "nsfw_detection": False,
        },
    )
    tools_enabled = Column(JSON, default={})
    last_updated = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # Added indexes for configuration lookups
        Index("idx_botconfig_group_id", "group_id"),
        Index("idx_botconfig_last_updated", "last_updated"),
    )

    group = relationship("Group", back_populates="bot_config")


# -------------------
# Group Context Document Chunks for RAG
# -------------------
class GroupContextDoc(Base):
    __tablename__ = "group_context_docs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id = Column(String, nullable=False, index=True)
    document_id = Column(UUID(as_uuid=True), ForeignKey("context_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    uploader_id = Column(BigInteger, ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True, index=True)
    source_type = Column(String, nullable=False)
    source_name = Column(String, nullable=True)
    content = Column(Text, nullable=False)
    summary = Column(Text, nullable=True)
    embedding = Column(Vector(1536))
    token_count = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_group_context_docs_group_id", "group_id"),
        # ivfflat index for ANN search on embedding (requires pgvector extension)
        Index(
            "idx_group_context_docs_embedding_ivfflat",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_with={"lists": 100},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        CheckConstraint(
            "source_type IN ('file', 'link', 'text')",
            name="ck_group_context_docs_source_type"
        ),
    )

    uploader = relationship("User", backref="uploaded_context_docs")
    parent_doc = relationship("ContextDocument", back_populates="chunks")


# -------------------
# Context Documents (metadata)
# -------------------
class ContextDocument(Base):
    __tablename__ = "context_documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id = Column(String, nullable=False, index=True)
    uploader_id = Column(BigInteger, ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True, index=True)
    source_type = Column(String, nullable=False)  # file | link | text
    source_name = Column(String)
    original_name = Column(String)
    url = Column(Text)
    num_chunks = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_context_documents_group_id", "group_id"),
    )
    chunks = relationship("GroupContextDoc", back_populates="parent_doc", cascade="all, delete-orphan")


def match_group_context_docs(session, query_vector, group_id, threshold=0.4, limit=5):
    """
    Perform a vector similarity search over group context docs for a given group.
    Returns list of (GroupContextDoc, similarity_score).
    """
    # Use SQLAlchemy select and literal_column for cosine similarity
    similarity = literal_column(f"1 - (embedding <=> :query_vector)")
    stmt = (
        select(GroupContextDoc, similarity.label("similarity"))
        .where(GroupContextDoc.group_id == group_id)
        .where(similarity > threshold)
        .order_by(desc("similarity"))
        .limit(limit)
    )
    results = session.execute(
        stmt,
        {"query_vector": query_vector}
    ).all()
    # Each row is (GroupContextDoc, similarity)
    return results