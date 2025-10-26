# MyAgent - Local Testing Guide

## 🚀 Quick Start (Polling Mode - Recommended for Local Testing)

### Prerequisites
1. Python 3.10+
2. PostgreSQL with pgvector extension
3. Redis
4. Telegram Bot Token (from @BotFather)
5. OpenAI API Key

### Step 1: Set Up Environment

```bash
# Clone/navigate to project
cd "/Users/user/Desktop/myagent copy"

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Step 2: Configure Environment Variables

Create `.env.local`:

```bash
# Telegram
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here

# OpenAI
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_MODEL=gpt-4o-mini
EMBEDDING_MODEL=text-embedding-3-small

# Database
DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/myagent

# Redis
REDIS_URL=redis://localhost:6379/0

# Storage (Optional - for media processing)
SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_key
SUPABASE_BUCKET=media

# Firecrawl (Optional - for link summarization)
FIRECRAWL_API_KEY=your_firecrawl_api_key
```

### Step 3: Start Required Services

**Terminal 1 - Start PostgreSQL**:
```bash
# macOS (Homebrew)
brew services start postgresql@14

# Or using Docker
docker run -d \
  --name myagent-postgres \
  -e POSTGRES_PASSWORD=password \
  -e POSTGRES_DB=myagent \
  -p 5432:5432 \
  ankane/pgvector
```

**Terminal 2 - Start Redis**:
```bash
# macOS (Homebrew)
brew services start redis

# Or using Docker
docker run -d \
  --name myagent-redis \
  -p 6379:6379 \
  redis:7-alpine
```

### Step 4: Initialize Database

```bash
# Create tables
python init_db.py
```

Expected output:
```
Database tables created successfully!
```

### Step 5: Run Bot in Polling Mode

```bash
python dev_run.py
```

Expected output:
```
{"timestamp": "...", "level": "INFO", "message": "🚀 Starting MyAgent Telegram Bot (POLLING MODE - Development)"}
{"timestamp": "...", "level": "INFO", "message": "✅ Redis cache initialized"}
{"timestamp": "...", "level": "INFO", "message": "✅ All handlers registered"}
{"timestamp": "...", "level": "INFO", "message": "📡 Starting polling (press Ctrl+C to stop)..."}
```

---

## 🧪 Testing the Bot

### 1. Create a Test Group

1. Open Telegram
2. Create a new group
3. Add your bot to the group
4. Make your bot an admin (required for message management)

### 2. Initialize the Group

In the Telegram group, send:
```
/init_group
```

Expected response:
```
✅ Group Test Group successfully initialized!
Members synced and configuration ready.
```

### 3. Configure the Bot

Send:
```
/config
```

You should see an interactive menu with:
- Change Tone 🔁
- Edit Threshold 📈
- Edit Rules ✏️
- Edit Group Description 📝
- Toggle Features ⚙️
- Cancel ❌
- Save ✅

Configure as needed, then click **Save ✅**.

### 4. Test Message Processing

**Test 1: Normal Message**
```
Hello everyone! How's it going?
```

Check logs for:
```
{"level": "INFO", "message": "Router intent=chat conf=0.95 evidence=..."}
```

**Test 2: Question (RAG)**
```
What is this group about?
```

Bot should reply (if group context exists) or say "I'm not sure".

**Test 3: Spam Message**
```
🚨 BUY CRYPTO NOW! Click here: http://scam.com 🚨
```

Check logs for:
```
{"level": "WARNING", "message": "Spam detected for user ... | confidence=0.95"}
```

Bot should delete the message (if moderation enabled in config).

### 5. Add Context (for RAG)

Send:
```
/add_context
```

Choose an option:
- **📄 Upload File** - Upload a PDF, DOCX, or text file
- **🔗 Add Link** - Send a URL to crawl
- **✏️ Add Text** - Send plain text

After adding, click **Save ✅**.

Now questions should be answered based on the added context.

---

## 🔍 Verify Everything Works

### Check Redis Cache

```bash
redis-cli

# Check cached groups
KEYS "group:*"

# Check cached messages
KEYS "msg:*"

# Check user data
KEYS "user:*"
```

### Check Database

```bash
psql -d myagent -U postgres

# Check groups
SELECT id, chat_id, name FROM groups;

# Check messages
SELECT id, user_id, group_id, message_type, content 
FROM messages 
ORDER BY created_at DESC 
LIMIT 10;

# Check spam results
SELECT message_id, spam, confidence, category, reason 
FROM spam_results 
ORDER BY created_at DESC 
LIMIT 5;

# Check router results
SELECT message_id, intent, confidence, is_group_qna_eligible 
FROM router_results 
ORDER BY created_at DESC 
LIMIT 5;
```

### Check Logs

Logs are in JSON format. Watch them with:

```bash
python dev_run.py 2>&1 | grep -i "error\|warning\|spam\|router\|rag"
```

Or use `jq` for pretty output:

```bash
python dev_run.py 2>&1 | jq 'select(.level == "WARNING" or .level == "ERROR")'
```

---

## 🐛 Common Issues

### Issue 1: "No Redis connection"

**Solution**:
```bash
# Check Redis is running
redis-cli ping
# Should return: PONG

# If not running:
brew services start redis
# or
docker start myagent-redis
```

### Issue 2: "Database connection failed"

**Solution**:
```bash
# Check PostgreSQL is running
psql -d myagent -U postgres -c "SELECT 1;"

# If fails, start PostgreSQL:
brew services start postgresql@14
# or
docker start myagent-postgres

# Verify DATABASE_URL in .env.local matches your setup
```

### Issue 3: "TELEGRAM_BOT_TOKEN not set"

**Solution**:
```bash
# Verify .env.local exists and has the token
cat .env.local | grep TELEGRAM_BOT_TOKEN

# If missing, add it:
echo "TELEGRAM_BOT_TOKEN=your_token_here" >> .env.local
```

### Issue 4: "Import errors"

**Solution**:
```bash
# Reinstall dependencies
pip install -r requirements.txt

# Verify Python version
python --version  # Should be 3.10+

# Clear Python cache
find . -type d -name "__pycache__" -exec rm -r {} +
find . -type f -name "*.pyc" -delete
```

### Issue 5: "Bot doesn't respond to messages"

**Solution**:
1. Verify bot is added to the group
2. Verify bot is an admin
3. Check `/init_group` was run
4. Check `/config` was run and saved
5. Check logs for errors

---

## 🎯 Full Flow Test Script

Create `test_full_flow.py`:

```python
"""
Manual test script - run this after bot is running.
Tests the full message flow: receive → spam check → route → RAG.
"""
import asyncio
from core.di import container
from adapter.context_builder import build_context

async def test_flow():
    # Simulate a message
    new_message = {
        "id": 999,
        "type": "text",
        "text": "What is Python?",
        "user_id": 123456,
        "group_id": -100123456789,
    }
    
    # Build context
    ctx = await build_context(
        user_id=new_message["user_id"],
        group_id=new_message["group_id"],
        new_message=new_message
    )
    
    # Get services
    router_service = container.get("router_service")
    rag_service = container.get("rag_service")
    
    # Route intent
    result = await router_service.route(ctx)
    print(f"Router result: intent={result.intent.value}, confidence={result.confidence}")
    
    # If QnA, try RAG
    if result.intent.value == "qna":
        answer = await rag_service.answer(
            group_id=new_message["group_id"],
            question=new_message["text"]
        )
        print(f"RAG answer: {answer.answer if answer else 'None'}")

if __name__ == "__main__":
    asyncio.run(test_flow())
```

Run it:
```bash
python test_full_flow.py
```

---

## 📊 Performance Checks

### Measure Response Time

```python
import time
import asyncio
from adapter.context_builder import build_context
from core.di import container

async def benchmark():
    start = time.time()
    
    # Build context
    ctx = await build_context(123, -100123, {"id": 1, "type": "text", "text": "test"})
    print(f"Context build: {(time.time() - start)*1000:.2f}ms")
    
    # Router
    start = time.time()
    router = container.get("router_service")
    result = await router.route(ctx)
    print(f"Router classify: {(time.time() - start)*1000:.2f}ms")
    
    # RAG
    start = time.time()
    rag = container.get("rag_service")
    answer = await rag.answer(group_id=-100123, question="test")
    print(f"RAG answer: {(time.time() - start)*1000:.2f}ms")

asyncio.run(benchmark())
```

Expected times:
- Context build: 10-50ms
- Router classify: 200-800ms (LLM call)
- RAG answer: 300-1000ms (embedding + LLM call)

---

## ✅ Success Checklist

After testing, verify:

- [ ] Bot starts without errors
- [ ] `/init_group` works
- [ ] `/config` interactive menu works
- [ ] Normal messages are processed
- [ ] Questions trigger RAG (if context exists)
- [ ] Spam is detected and deleted (if moderation enabled)
- [ ] Redis cache is populated (`redis-cli KEYS "*"`)
- [ ] Database records messages (`psql` checks)
- [ ] Logs show JSON format
- [ ] No import errors
- [ ] Health check passes (for webhook mode)

---

## 🚀 Next Steps

Once local testing works:

1. **Deploy to production** using `docker-compose up`
2. **Set up webhook** with a public URL (use ngrok for testing)
3. **Add monitoring** (Prometheus, Grafana, Sentry)
4. **Run workers** for background tasks (embedding, cleanup)
5. **Scale horizontally** by running multiple bot instances

Good luck! 🎉

