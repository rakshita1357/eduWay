import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.apis.routes import router as api_router
from app.db.session import init_db
import os
from dotenv import load_dotenv
import logging

# Load .env for local development so ALLOWED_ORIGINS and other env vars are available
load_dotenv()

# Initialize App
app = FastAPI(
    title="Agentic Learning Path Generator",
    description="Backend for AI-driven personalized education using Gemini 2.5 Flash.",
    version="1.0.0"
)

logger = logging.getLogger("uvicorn.error")

# Default CORS origins (keeps previous values)
_default_origins = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:8082",
    "http://localhost:8080",
    # Common 127.0.0.1 aliases for local dev
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:8082",
    "http://127.0.0.1:8080",
    "http://127.0.0.1:8001",
    # Docker/LAN network aliases
    "http://172.18.0.1:8080",
    "http://172.20.75.97:8080",
    # Lovable preview URL
    "https://id-preview--3a7b2998-fc47-4b75-9b46-fbfbfd416a18.lovable.app",

    # Common deployed/lovable frontend used in preview/testing
    "https://agent-path-forge.lovable.app",

    # Render deployed frontend
    "https://edupath-fe-v1.onrender.com",
    "https://agent-path-forge.onrender.com",

    # If you have final deployed frontend, add here:
    "https://your-frontend.lovable.app",
]

# Allow configuring additional origins via environment variable ALLOWED_ORIGINS
# Format: comma-separated list of origins. Use '*' to allow all origins (not recommended for production).
_allowed_env = os.getenv("ALLOWED_ORIGINS", "")
_allow_all = os.getenv("ALLOW_ALL_ORIGINS", "false").lower() in ("1", "true", "yes")

if _allow_all:
    origins = ["*"]
else:
    if _allowed_env:
        # If '*' is provided explicitly, allow all origins
        if _allowed_env.strip() == '*':
            origins = ["*"]
        else:
            env_list = [o.strip() for o in _allowed_env.split(',') if o.strip()]
            # Merge while preserving defaults first, then unique additions from env
            origins = _default_origins + [o for o in env_list if o not in _default_origins]
    else:
        origins = _default_origins

# Safety: if origins somehow ends up empty, fallback to '*' and log — this prevents missing CORS headers.
try:
    if not origins:
        logger.warning("Resolved CORS origins list is empty; falling back to allow all origins '*'.")
        origins = ["*"]
except Exception:
    # If logger isn't configured yet, just ensure origins is non-empty
    if not origins:
        origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(api_router, prefix="/api/v1", tags=["Roadmap Generation"])

# Health check endpoint
@app.get("/health")
def health():
    import datetime
    return {"status": "healthy", "timestamp": datetime.datetime.utcnow().isoformat()}

# Root endpoint
@app.get("/")
def root():
    return {"message": "Agentic Learning System API is Online 🚀"}


@app.on_event("startup")
def on_startup():
    # Initialize the DB (create tables if they don't exist)
    init_db()
    # Log resolved CORS origins for debugging in deployed logs
    try:
        logger.info(f"CORS origins: {origins}")
    except Exception:
        pass

    # Log whether GEMINI_API_KEY is present in environment (masked) so you can confirm Render provided it
    try:
        gem_key = os.getenv("GEMINI_API_KEY")
        if gem_key:
            # mask the key for safe logging
            masked = (gem_key[:4] + "...." + gem_key[-4:]) if len(gem_key) > 8 else "****"
            logger.info(f"GEMINI_API_KEY present (masked): {masked}")
        else:
            logger.warning("GEMINI_API_KEY is NOT present in environment at startup.")
    except Exception:
        logger.exception("Error reading GEMINI_API_KEY from environment")


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)