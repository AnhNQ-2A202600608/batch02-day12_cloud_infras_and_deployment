# Deployment Information

## Public URL
https://day12-production-agent.up.railway.app

## Platform
Railway / Render / GCP Cloud Run

## Test Commands

### 1. Health Check (Liveness Probe)
```bash
curl https://day12-production-agent.up.railway.app/health
# Expected Output:
# {"status":"ok","version":"1.0.0","environment":"production","uptime_seconds":12.5,"checks":{"llm":"mock"},"timestamp":"2026-06-12T10:00:00Z"}
```

### 2. Readiness Check (Readiness Probe)
```bash
curl https://day12-production-agent.up.railway.app/ready
# Expected Output:
# {"ready":true}
```

### 3. API Test (Without Authentication - Should Fail)
```bash
curl -i -X POST https://day12-production-agent.up.railway.app/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What is Docker?"}'
# Expected Output: HTTP/1.1 401 Unauthorized
```

### 4. API Test (With Authentication - Should Succeed)
```bash
curl -X POST https://day12-production-agent.up.railway.app/ask \
  -H "X-API-Key: your-secret-key" \
  -H "Content-Type: application/json" \
  -d '{"question": "What is Docker?", "session_id": "test-session-1"}'
# Expected Output:
# {"question":"What is Docker?","answer":"Container là cách đóng gói app để chạy ở mọi nơi...","model":"gpt-4o-mini","timestamp":"2026-06-12T10:00:00Z","session_id":"test-session-1"}
```

### 5. Rate Limiting Test (Send > 20 requests in 1 min)
Run the following loop in terminal:
```bash
for i in {1..25}; do
  curl -X POST https://day12-production-agent.up.railway.app/ask \
    -H "X-API-Key: your-secret-key" \
    -H "Content-Type: application/json" \
    -d '{"question": "test"}'
done
# Expected output after the 20th request: HTTP/1.1 429 Too Many Requests
```

---

## Environment Variables Set
Ensure the following variables are configured on the deployment platform:
- `ENVIRONMENT`: `production` (enables API key validation)
- `PORT`: `8000` (automatically set by Railway/Render)
- `AGENT_API_KEY`: `your-secret-key` (used for authentication)
- `REDIS_URL`: `redis://redis:6379/0` (connection string for Redis service)
- `RATE_LIMIT_PER_MINUTE`: `20` (max requests per minute)
- `DAILY_BUDGET_USD`: `5.0` (max budget per user/day)

---

## Screenshots
Please check the `screenshots/` directory for visual confirmations of:
- `dashboard.png`: Active services running on the Railway dashboard.
- `running.png`: Service terminal logs showing successful startup and connections.
- `test.png`: Successful API endpoints calls (health, ready, ask).
