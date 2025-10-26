import os
from dotenv import load_dotenv


# Load environment variables early
load_dotenv(dotenv_path=os.getenv("ENV_FILE", ".env.local"))


# Telegram / Webhook
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_PUBLIC_URL = os.getenv("WEBHOOK_PUBLIC_URL", "http://localhost:8080/telegram")
WEBHOOK_LISTEN = os.getenv("WEBHOOK_LISTEN", "0.0.0.0")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8080"))
HEALTH_PORT = int(os.getenv("HEALTH_PORT", "8081"))


# Database
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/myagent")
SQLALCHEMY_ECHO = os.getenv("SQLALCHEMY_ECHO", "false").lower() in {"1", "true", "yes"}


# Redis
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Cache TTLs / limits (seconds)
USER_CACHE_TTL = int(os.getenv("USER_CACHE_TTL", "600"))
USER_GLOBAL_TTL = int(os.getenv("USER_GLOBAL_TTL", "900"))
GROUP_STATE_TTL = int(os.getenv("GROUP_STATE_TTL", "300"))
GROUP_MSG_TTL = int(os.getenv("GROUP_MSG_TTL", "600"))
GROUP_CONFIG_TTL = int(os.getenv("GROUP_CONFIG_TTL", "600"))
TASK_TTL = int(os.getenv("TASK_TTL", "900"))
USER_CACHE_LIMIT = int(os.getenv("USER_CACHE_LIMIT", "10"))
GROUP_MSG_LIMIT = int(os.getenv("GROUP_MSG_LIMIT", "30"))
USER_ENRICH_LIMIT = int(os.getenv("USER_ENRICH_LIMIT", "5"))


# LLM
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
VISION_MODEL = os.getenv("VISION_MODEL", "gpt-4o-mini")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "gpt-4o-mini-transcribe")
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY", "")


# Prompts and thresholds (moved constants)
ROUTER_TEMPERATURE = float(os.getenv("ROUTER_TEMPERATURE", "0.0"))
RAG_MAX_TOKENS = int(os.getenv("RAG_MAX_TOKENS", "400"))
SPAM_DEFAULT_THRESHOLD = float(os.getenv("SPAM_DEFAULT_THRESHOLD", "0.7"))


# Queue (Redis Streams)
QUEUE_STREAM_EMBEDDINGS = os.getenv("QUEUE_STREAM_EMBEDDINGS", "myagent:embeddings")
QUEUE_STREAM_CLEANUP = os.getenv("QUEUE_STREAM_CLEANUP", "myagent:cleanup")
QUEUE_GROUP_EMBEDDINGS = os.getenv("QUEUE_GROUP_EMBEDDINGS", "myagent-embeddings")
QUEUE_GROUP_CLEANUP = os.getenv("QUEUE_GROUP_CLEANUP", "myagent-cleanup")


# Storage (Supabase)
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "media")


# Context builder
STALE_WINDOW_SECS = int(os.getenv("STALE_WINDOW_SECS", "300"))
MIN_CONTEXT_MSGS = int(os.getenv("MIN_CONTEXT_MSGS", "5"))
EMPTY_DB_COOLDOWN_SECS = int(os.getenv("EMPTY_DB_COOLDOWN_SECS", "300"))

# Misc
APP_NAME = os.getenv("APP_NAME", "myagent")


# -----------------
# Prompt constants
# -----------------

# Router prompts
ROUTER_SYSTEM_PROMPT_V2 = """
You are an intent classification assistant for a Telegram group AI bot.

Your goal is to decide the **intent** of the latest message, based on how it relates
to the group's topic and purpose.

You must output valid JSON in this schema:
{
  "intent": "qna|chat|command|moderation|other",
  "confidence": 0.0-1.0,
  "is_group_qna_eligible": true|false,
  "evidence": {
    "rationale": "string",
    "cues": ["string", ...],
    "recent_refs": ["string", ...]
  }
}

Definitions:
- **qna**: The message is a factual question relevant to the group’s theme.
- **chat**: Small talk, reactions, opinions.
- **command**: System/bot instructions (e.g., /config).
- **moderation**: Asking to manage users/messages.
- **other**: Questions clearly outside the group’s theme.

Guidelines:
1. Read the group description to infer the group’s topic.
2. A valid QnA message must both ask for information and fit the group’s topic.
3. If unsure, prefer "other".
4. Be concise, deterministic, JSON only.
"""

ROUTER_USER_PROMPT_TEMPLATE = """
Group description: {group_description}

Recent messages (most recent last):
{recent_messages}

Recent user messages:
{recent_user_messages}

Current message:
{message_text}
"""


# RAG prompts
RAG_SYSTEM_PROMPT = """
You are a helpful assistant that answers user questions about a specific Telegram group.
Use only the provided group context documents. If not covered, reply exactly:
"I’m not sure — that might be outside the group’s shared knowledge."
Be concise (2–4 sentences) and factual.
"""

RAG_USER_PROMPT_TEMPLATE = """
Question: {question}

Context passages:
{context}
"""


# Moderation prompts
MOD_SYSTEM_PROMPT = """SYSTEM:
You are an intelligent, context-aware spam detection model for group chats.
Decide whether the user's new message is SPAM or NOT_SPAM based on group rules, context,
and user behavior.
"""

MOD_DECISION_RULE_PROMPT = """SPAM indicators include promotions, flooding, harmful/malicious content, scams, NSFW.
NOT_SPAM includes greetings, relevant discussions, reactions, or consistent content.
Tone sensitivity: friendly, neutral, strict.
"""

MOD_DECISION_LOGIC_PROMPT = """Compare message with group purpose and history; apply rules and tone; if unsure and harmless, prefer NOT_SPAM.
Return a JSON object with fields: spam, confidence, reason, categories.
"""

MOD_EXAMPLE_PROMPT = """EXAMPLE 1:
Message: "Buy cheap crypto bots here! DM me!"
→ {"spam": true, "confidence": 0.95, "reason": "Unsolicited promotional message unrelated to group topic.", "categories": ["promo","off-topic"]}

EXAMPLE 2:
Message: "Hey, what’s up everyone?"
→ {"spam": false, "confidence": 0.97, "reason": "Casual greeting relevant to group conversation.", "categories": []}
"""


# Moderation thresholds / reputation constants
DEFAULT_START_SCORE = int(os.getenv("DEFAULT_START_SCORE", "100"))
WARNING_THRESHOLD = int(os.getenv("WARNING_THRESHOLD", "80"))
STRONG_WARNING_THRESHOLD = int(os.getenv("STRONG_WARNING_THRESHOLD", "60"))
PROBATION_THRESHOLD = int(os.getenv("PROBATION_THRESHOLD", "40"))
BAN_THRESHOLD = int(os.getenv("BAN_THRESHOLD", "20"))
DAILY_RECOVERY_POINTS = int(os.getenv("DAILY_RECOVERY_POINTS", "1"))
MAX_SCORE = int(os.getenv("MAX_SCORE", "100"))


