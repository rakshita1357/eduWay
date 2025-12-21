#!/bin/bash
# Test the updated backend endpoints

echo "Testing renamed endpoints to verify ad blocker fix..."
echo ""

echo "1. Testing /api/v1/status (was /health):"
curl -s http://127.0.0.1:8001/api/v1/status | jq . || curl -s http://127.0.0.1:8001/api/v1/status
echo -e "\n"

echo "2. Testing /api/v1/sessions (was /conversations) with sample profile:"
curl -s -X POST http://127.0.0.1:8001/api/v1/sessions \
  -H "Content-Type: application/json" \
  -H "Origin: http://172.18.0.1:8080" \
  -d '{
    "name": "Test User",
    "current_role": "student",
    "target_role": "full stack developer",
    "current_skills": ["HTML", "CSS"],
    "preferred_style": "Video",
    "time_commitment": "2 hours/week",
    "experience_level": "Beginner",
    "goal": "Learn web development"
  }' | head -c 200
echo -e "\n...(truncated for readability)\n"

echo "✅ If you see JSON responses above, the backend is working!"
echo "❌ If you see errors, check that GEMINI_API_KEY is set in .env"
echo ""
echo "Next steps:"
echo "1. Update your frontend to use the new endpoints (see FRONTEND_UPDATES_REQUIRED.md)"
echo "2. Restart your frontend"
echo "3. Test in browser - ERR_BLOCKED_BY_CLIENT should be gone!"

