# Deployment Information

## Public URL
https://agent-on-railway-production.up.railway.app

## Platform
Railway

## Test Commands

### Health Check
```bash
curl https://agent-on-railway-production.up.railway.app/health
# Expected: {"status": "ok"}
```

### API Test (with mock/no authentication for current basic app, but prepared for key-auth in production)
```bash
curl -X POST https://agent-on-railway-production.up.railway.app/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Hello"}'
```

## Environment Variables Set
- PORT
- AGENT_API_KEY

## Screenshots
- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)
