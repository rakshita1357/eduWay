# Backend Endpoint Changes - Frontend Update Required

## ✅ BACKEND CHANGES COMPLETE

I've renamed all ad-blocker-triggering endpoints to avoid ERR_BLOCKED_BY_CLIENT errors.

---

## 📋 Frontend URL Changes Needed

Update your frontend API client (client.ts or wherever you define API URLs) with these changes:

### Before → After

| Old Endpoint | New Endpoint | Method | Purpose |
|-------------|--------------|--------|---------|
| `/health` | `/status` | GET | Health check |
| `/conversations` | `/sessions` | POST | Start new learning session |
| `/conversations/{id}/messages` | `/sessions/{id}/messages` | POST | Post message to session |
| `/conversations/{id}/progress` | `/sessions/{id}/progress` | POST | Mark module progress |
| `/conversations/{id}/regenerate` | `/sessions/{id}/regenerate` | POST | Regenerate roadmap |

---

## 🔧 Example Frontend Changes

### Old client.ts code:
```typescript
// Health check
const healthCheck = () => axios.get(`${API_BASE_URL}/health`);

// Start conversation
const startConversation = (profile) => 
  axios.post(`${API_BASE_URL}/conversations`, profile);

// Post message
const postMessage = (conversationId, message) =>
  axios.post(`${API_BASE_URL}/conversations/${conversationId}/messages`, message);
```

### New client.ts code:
```typescript
// Health check
const healthCheck = () => axios.get(`${API_BASE_URL}/status`);

// Start session
const startSession = (profile) => 
  axios.post(`${API_BASE_URL}/sessions`, profile);

// Post message
const postMessage = (sessionId, message) =>
  axios.post(`${API_BASE_URL}/sessions/${sessionId}/messages`, message);
```

---

## 🚀 Full API Base URL

Make sure your `API_BASE_URL` includes `/api/v1`:

```typescript
const API_BASE_URL = "http://127.0.0.1:8001/api/v1";
```

So full URLs become:
- `http://127.0.0.1:8001/api/v1/status`
- `http://127.0.0.1:8001/api/v1/sessions`
- etc.

---

## ✅ Testing After Update

1. **Restart your backend** (if running):
   ```bash
   # Kill old process if running on port 8001
   kill $(lsof -t -i:8001)
   
   # Start fresh
   source .venv/bin/activate
   python -m uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
   ```

2. **Test with curl** (bypass browser to verify backend works):
   ```bash
   # Test status endpoint
   curl http://127.0.0.1:8001/api/v1/status
   # Should return: {"status":"active","service":"Agentic Learning Backend"}
   
   # Test sessions endpoint
   curl -X POST http://127.0.0.1:8001/api/v1/sessions \
     -H "Content-Type: application/json" \
     -H "Origin: http://172.18.0.1:8080" \
     -d '{
       "name": "Test User",
       "current_role": "student",
       "target_role": "developer",
       "current_skills": ["Python basics"],
       "preferred_style": "Video",
       "time_commitment": "2 hours/week",
       "experience_level": "Beginner",
       "goal": "Learn FastAPI"
     }'
   ```

3. **Update your frontend** with the new endpoint names

4. **Rebuild/restart your frontend**

5. **Open browser** at `http://172.18.0.1:8080/roadmaps/new`

6. **Check DevTools Network tab** - you should now see:
   - ✅ Status 200 for `/api/v1/status`
   - ✅ Status 201 for `/api/v1/sessions`
   - ❌ NO MORE `ERR_BLOCKED_BY_CLIENT`

---

## 🔍 Important Notes

### About "Not Secure" Warning
- **This is NORMAL** for HTTP (non-HTTPS) development
- It's **NOT** the cause of your errors
- To remove it (optional for dev):
  - Use `localhost` instead of IP addresses
  - Or set up a local SSL certificate (overkill for dev)
  - Or ignore it - doesn't affect functionality

### Response Field Changes
The `/sessions` endpoint now returns:
```json
{
  "roadmap_id": 123,
  "session_id": 456  // was "conversation_id"
}
```

Update your frontend to use `session_id` instead of `conversation_id`.

---

## 📝 Summary

**The "Not Secure" warning is NOT the problem.**

**The real issue was:** Ad blockers block URLs containing "conversations" and "health"

**The fix:** Renamed to "sessions" and "status"

**What you need to do:**
1. ✅ Backend is done (I updated it)
2. 🔄 Update your frontend URLs as shown above
3. 🔄 Restart backend with the command above
4. 🔄 Rebuild/restart frontend
5. ✅ Test - should work without ad blocker issues!

---

## 🆘 If Still Not Working

1. Check backend is running on correct port:
   ```bash
   lsof -iTCP:8001 -sTCP:LISTEN
   ```

2. Check backend logs for CORS origins - should include your frontend IP

3. Try with curl to verify backend works (see Testing section above)

4. Check browser DevTools Console for any new errors

5. Verify frontend API_BASE_URL includes `/api/v1` prefix

