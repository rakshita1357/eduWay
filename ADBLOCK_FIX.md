# Fix ERR_BLOCKED_BY_CLIENT (Ad Blocker Issue)

## ✅ SOLUTION APPLIED - Backend Updated!

The backend endpoints have been renamed to avoid ad blocker interference:
- `/health` → `/status` ✅
- `/conversations` → `/sessions` ✅
- `/conversations/{id}/*` → `/sessions/{id}/*` ✅

**Your frontend needs to be updated to use the new endpoints.**
See `FRONTEND_UPDATES_REQUIRED.md` for detailed instructions.

---

## Original Problem
Your browser's ad blocker (uBlock Origin, AdBlock Plus, Brave Shields, etc.) was blocking requests to:
- `/health`
- `/conversations`

These endpoint names match common ad/tracker patterns and get automatically blocked.

## Quick Solutions (Choose One)

### Solution 1: Disable Ad Blocker for Your Dev Environment (Easiest)
**Steps:**
1. Open your browser's ad blocker extension
2. Add an exception/whitelist for:
   - `http://127.0.0.1:8001`
   - `http://172.18.0.1:8080`
3. Reload the page

**For uBlock Origin:**
- Click the uBlock icon → Click the big power button to disable for this site
- Or add `127.0.0.1` and `172.18.0.1` to the whitelist

**For Brave Browser:**
- Click the Brave Shields icon in address bar → Turn off Shields for this site

**For Chrome/Edge with AdBlock Plus:**
- Click AdBlock icon → "Don't run on pages on this domain"

---

### Solution 2: Use Different Endpoint Names (Permanent Fix)
Rename the blocked endpoints to avoid filter lists.

**Backend changes needed:**

```python
# In app/apis/routes.py, change:
@router.post("/conversations", ...)  
# to:
@router.post("/sessions", ...)

@router.get("/health")
# to:
@router.get("/status")
```

**Frontend changes needed:**
Update your API client to use the new paths:
- `/api/v1/conversations` → `/api/v1/sessions`
- `/health` → `/status`

---

### Solution 3: Temporary Wildcard CORS (Dev Only - Not Recommended)
Set environment variable to allow all origins:

```bash
export ALLOW_ALL_ORIGINS=true
python -m uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

**Warning:** This doesn't fix the ad blocker issue, but helps verify CORS isn't the problem.

---

## Verify the Fix

After applying a solution, test:

```bash
# Check if backend is reachable
curl http://127.0.0.1:8001/api/v1/health
# or if renamed:
curl http://127.0.0.1:8001/api/v1/status

# Should return: {"status":"active","service":"Agentic Learning Backend"}
```

Then reload your frontend and check the browser console - the ERR_BLOCKED_BY_CLIENT errors should be gone.

---

## Current CORS Origins (Already Configured)
✅ Your backend already allows these origins:
- `http://localhost:3000`, `http://localhost:5173`, `http://localhost:8080`, `http://localhost:8082`
- `http://127.0.0.1:3000`, `http://127.0.0.1:5173`, `http://127.0.0.1:8080`, `http://127.0.0.1:8082`, `http://127.0.0.1:8001`
- `http://172.18.0.1:8080` (Docker bridge network)
- `http://172.20.75.97:8080` (LAN IP)

---

## Additional Debugging

**Check if backend is running:**
```bash
lsof -iTCP:8001 -sTCP:LISTEN -P -n
```

**View backend logs for CORS info:**
Your startup logs will show: `CORS origins: [...]`

**Test with curl to bypass browser:**
```bash
curl -X POST http://127.0.0.1:8001/api/v1/conversations \
  -H "Content-Type: application/json" \
  -H "Origin: http://172.18.0.1:8080" \
  -d '{"goal":"Learn Python","current_skills":["Basic programming"],"preferred_style":"Video","time_commitment":"2 hours/week","experience_level":"Beginner"}'
```

If curl works but browser doesn't → **it's definitely the ad blocker**.

---

## Recommended Action
1. **Right now:** Disable ad blocker for your dev domains (Solution 1)
2. **Later:** Rename endpoints to avoid future issues (Solution 2)

