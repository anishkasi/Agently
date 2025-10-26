# Docker Testing Guide for MyAgent

## 🐳 Testing Containerized Version

### Prerequisites
- Docker Desktop installed (or Docker + Docker Compose)
- Your bot working in dev mode (`python dev_run.py`)
- Public URL for webhook (ngrok for testing, or actual domain)

---

## 📋 Step 1: Configure Environment

### Create `.env.docker` file:

```bash
cp .env.docker.example .env.docker
```

Edit `.env.docker` with your values:

```bash
# REQUIRED
TELEGRAM_BOT_TOKEN=7675016101:AAHL0ubBaLv2SqriPqEZBm6ziQF14mGLN_4
OPENAI_API_KEY=sk-...
DATABASE_URL=postgresql+asyncpg://postgres.[ref].supabase.co:5432/postgres

# For local testing with ngrok
WEBHOOK_PUBLIC_URL=https://abc123.ngrok.io

# Optional
SUPABASE_URL=https://[ref].supabase.co
SUPABASE_KEY=eyJh...
FIRECRAWL_API_KEY=fc-...
```

---

## 🚀 Step 2: Build and Start Services

### Option A: Local Testing (with ngrok)

**Terminal 1 - Start ngrok:**
```bash
ngrok http 8080
```

Copy the HTTPS URL (e.g., `https://abc123.ngrok.io`) and update it in `.env.docker`:
```bash
WEBHOOK_PUBLIC_URL=https://abc123.ngrok.io
```

**Terminal 2 - Start Docker:**
```bash
# Build and start all services
docker-compose up --build

# Or run in background
docker-compose up --build -d
```

### Option B: Production with Domain

Update `.env.docker`:
```bash
WEBHOOK_PUBLIC_URL=https://yourdomain.com
```

Start services:
```bash
docker-compose up -d
```

---

## 🔍 Step 3: Verify Everything is Running

### Check container status:
```bash
docker-compose ps
```

Expected output:
```
NAME                  STATUS              PORTS
myagent-bot-1         Up (healthy)        0.0.0.0:8080->8080/tcp
myagent-redis-1       Up (healthy)        0.0.0.0:6379->6379/tcp
myagent-nginx-1       Up                  0.0.0.0:80->80/tcp
myagent-worker_*      Up
```

### Check health endpoint:
```bash
curl http://localhost:8080/health
```

Expected: `{"status":"ok"}`

### Check logs:
```bash
# All services
docker-compose logs -f

# Just the bot
docker-compose logs -f bot

# Last 100 lines
docker-compose logs --tail=100 bot
```

---

## 🧪 Step 4: Test the Bot

### 1. Test /init_group
In your Telegram group:
```
/init_group
```

Check logs:
```bash
docker-compose logs bot | grep init_group
```

### 2. Test /config
```
/config
```
Configure the bot and save.

### 3. Test Message Processing
Send a normal message and check logs:
```bash
docker-compose logs -f bot | grep -i "router\|spam"
```

### 4. Test Spam Detection
Send a spam message (e.g., "Buy crypto now! DM me!")

Check if it gets deleted and logs show spam detection.

### 5. Test /add_context
```
/add_context
```
Upload a PDF or send a link.

Check worker logs:
```bash
docker-compose logs -f worker_embeddings
```

### 6. Test RAG
Ask a question related to your uploaded context.

Bot should reply with an answer from the context.

---

## 🔧 Step 5: Common Issues & Fixes

### Issue 1: Bot not starting
**Check logs:**
```bash
docker-compose logs bot
```

**Common causes:**
- Missing environment variables
- Database connection failed
- Redis connection failed

**Fix:**
```bash
# Rebuild and restart
docker-compose down
docker-compose up --build
```

### Issue 2: Webhook not receiving updates
**Check webhook status:**
```bash
curl https://api.telegram.org/bot<YOUR_TOKEN>/getWebhookInfo
```

**Should show:**
```json
{
  "url": "https://abc123.ngrok.io/telegram",
  "has_custom_certificate": false,
  "pending_update_count": 0
}
```

**Fix:**
```bash
# Delete webhook and set again
curl https://api.telegram.org/bot<YOUR_TOKEN>/deleteWebhook
docker-compose restart bot
```

### Issue 3: Database connection failed
**Error:** `could not connect to server`

**Fix:** Verify `DATABASE_URL` in `.env.docker`:
- Should use `postgresql+asyncpg://` prefix
- For Supabase: include `?ssl=require` parameter
- Check IP allowlist in Supabase (allow 0.0.0.0/0 for testing)

### Issue 4: Redis connection failed
**Error:** `Connection refused` or `redis:6379`

**Fix:**
```bash
# Check Redis is running
docker-compose ps redis

# Restart Redis
docker-compose restart redis

# Check Redis logs
docker-compose logs redis
```

### Issue 5: Workers not processing
**Check if workers are running:**
```bash
docker-compose ps | grep worker
```

**Check worker logs:**
```bash
docker-compose logs worker_embeddings
docker-compose logs worker_cleanup
```

**Restart workers:**
```bash
docker-compose restart worker_embeddings worker_cleanup
```

---

## 📊 Step 6: Monitor Performance

### View resource usage:
```bash
docker stats
```

### Check Redis keys:
```bash
docker-compose exec redis redis-cli

# Inside Redis CLI:
KEYS *
INFO memory
DBSIZE
```

### Check database connections:
```sql
-- In your PostgreSQL client
SELECT * FROM pg_stat_activity WHERE datname = 'myagent';
```

---

## 🛑 Step 7: Stop & Clean Up

### Stop services (keep data):
```bash
docker-compose stop
```

### Stop and remove containers:
```bash
docker-compose down
```

### Stop and remove EVERYTHING (including volumes):
```bash
docker-compose down -v
```

### Remove images to save space:
```bash
docker-compose down --rmi all
```

---

## 🔄 Step 8: Update and Redeploy

### After code changes:

```bash
# Rebuild only bot service
docker-compose up -d --build bot

# Rebuild all services
docker-compose up -d --build

# View logs during update
docker-compose logs -f bot
```

---

## 📈 Production Deployment Tips

### 1. Use Proper Secrets Management
Don't commit `.env.docker` to git!

```bash
# Add to .gitignore
echo ".env.docker" >> .gitignore
```

### 2. Set Resource Limits
Add to `docker-compose.yml`:

```yaml
services:
  bot:
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 1G
        reservations:
          cpus: '0.5'
          memory: 512M
```

### 3. Enable Auto-restart
Already configured with `restart: unless-stopped`

### 4. Set Up Monitoring
Add Prometheus + Grafana:

```yaml
  prometheus:
    image: prom/prometheus
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
```

### 5. Use SSL/TLS
Update nginx config with Let's Encrypt certificates:

```nginx
listen 443 ssl;
ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;
```

---

## ✅ Success Checklist

- [ ] All containers running and healthy
- [ ] `/health` endpoint returns `{"status":"ok"}`
- [ ] Webhook receives updates (check logs)
- [ ] `/init_group` works
- [ ] `/config` saves settings
- [ ] Messages are processed (check router logs)
- [ ] Spam is detected and deleted
- [ ] `/add_context` uploads work
- [ ] RAG answers questions
- [ ] Workers process background tasks
- [ ] Redis cache is populated
- [ ] Database records messages

---

## 🎉 You're Done!

Your bot is now running in production-like environment with:
- ✅ Webhook mode (not polling)
- ✅ Health checks
- ✅ Auto-restart on failure
- ✅ Background workers
- ✅ Redis caching
- ✅ Nginx reverse proxy
- ✅ Isolated containers

**Ready for production deployment!** 🚀

