from fastapi import APIRouter, HTTPException, status, Depends
from sqlalchemy.orm import Session
from app.models.schemas import UserProfile, RoadmapResponse
from app.services.agent_service import AgentWorkflow
from app.db.session import get_session
from app.db import models as db_models
import datetime
import json
import os

router = APIRouter()

@router.post("/generate-roadmap", response_model=RoadmapResponse, status_code=status.HTTP_200_OK)
async def generate_roadmap(profile: UserProfile, db: Session = Depends(get_session)):
    """
    Triggers the Multi-Agent System to generate a personalized learning path.
    This will save the generated roadmap and logs to the database.
    """
    try:
        workflow = AgentWorkflow(db_session=db)
        result = await workflow.generate_learning_path(profile)
        return result
    except Exception as e:
        # In production, log the full error to backend logs
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent Swarm Failure: {str(e)}"
        )

@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def start_session(profile: UserProfile, db: Session = Depends(get_session)):
    """Start a learning session linked to a user/roadmap. Returns the saved roadmap id and session id."""
    try:
        workflow = AgentWorkflow(db_session=db)
        result = workflow.generate_learning_path_sync(profile)
        # result is a RoadmapResponse (pydantic) but already saved to DB inside workflow
        return {"roadmap_id": result.get("roadmap_id"), "session_id": result.get("conversation_id")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/sessions/{session_id}/messages", status_code=status.HTTP_201_CREATED)
def post_session_message(session_id: int, message: dict, db: Session = Depends(get_session)):
    """Post a message in a session; message should include 'sender' and 'text'."""
    try:
        # store message in agent logs table or a new session_messages table (simpler: AgentLog)
        # We'll attach the message as an AgentLog with agent_name=sender and action=text
        sender = message.get("sender", "user")
        text = message.get("text", "")
        log = db_models.AgentLog(roadmap_id=session_id, agent_name=sender, action=text, timestamp=datetime.datetime.utcnow().isoformat())
        db.add(log)
        db.commit()
        db.refresh(log)
        return {"message_id": log.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/sessions/{session_id}/progress", status_code=status.HTTP_201_CREATED)
def mark_module_progress(session_id: int, body: dict, db: Session = Depends(get_session)):
    """Mark a module as completed for a session. Body: {"module_id": int, "status": "completed"} """
    try:
        module_id = body.get("module_id")
        status_val = body.get("status", "completed")
        prog = db_models.ModuleProgress(roadmap_id=session_id, module_id=module_id, status=status_val, completed_at=datetime.datetime.utcnow().isoformat())
        db.add(prog)
        db.commit()
        db.refresh(prog)
        return {"progress_id": prog.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/sessions/{session_id}/regenerate", response_model=RoadmapResponse)
def regenerate_roadmap(session_id: int, db: Session = Depends(get_session)):
    """Regenerate a roadmap based on existing session progress and original profile."""
    try:
        # load existing roadmap and profile
        roadmap = db.query(db_models.Roadmap).filter(db_models.Roadmap.id == session_id).first()
        if not roadmap:
            raise HTTPException(status_code=404, detail="Session/Roadmap not found")

        profile_json = roadmap.profile
        if not profile_json:
            raise HTTPException(status_code=400, detail="Original profile not available for regeneration")

        # load profile and progress
        profile_data = json.loads(profile_json)
        profile = UserProfile(**profile_data)

        # collect completed module ids
        completed = db.query(db_models.ModuleProgress).filter(db_models.ModuleProgress.roadmap_id == session_id, db_models.ModuleProgress.status == 'completed').all()
        completed_module_ids = [c.module_id for c in completed]

        # pass progress to AgentWorkflow so it can consider already-completed modules
        workflow = AgentWorkflow(db_session=db)
        # Pass completed_module_ids into generation so agents get the progress context
        result = workflow.generate_learning_path_sync(profile, completed_module_ids=completed_module_ids)
        # result will save a new roadmap and return new ids; load and return the new roadmap response
        new_roadmap_id = result.get("roadmap_id")
        new_roadmap = db.query(db_models.Roadmap).filter(db_models.Roadmap.id == new_roadmap_id).first()
        # build a RoadmapResponse from DB
        if not new_roadmap:
            raise HTTPException(status_code=500, detail="Failed to create regenerated roadmap")

        # fetch market_analysis, modules, and logs to assemble response
        market_analysis = json.loads(new_roadmap.market_analysis)
        modules = []
        for m in db.query(db_models.Module).filter(db_models.Module.roadmap_id == new_roadmap.id).all():
            resources = []
            for r in db.query(db_models.Resource).filter(db_models.Resource.module_id == m.id).all():
                resources.append({"title": r.title, "url": r.url, "type": r.type, "duration": r.duration, "reason": r.reason})
            modules.append({"id": m.module_index, "module_name": m.module_name, "description": m.description, "skills_covered": json.loads(m.skills_covered), "resources": resources, "why_needed": m.why_needed, "estimated_time": m.estimated_time})

        logs = []
        for l in db.query(db_models.AgentLog).filter(db_models.AgentLog.roadmap_id == new_roadmap.id).all():
            logs.append({"agent_name": l.agent_name, "action": l.action, "timestamp": l.timestamp})

        return RoadmapResponse(market_analysis=market_analysis, roadmap=modules, agent_logs=logs)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/status")
def status_check():
    return {"status": "active", "service": "Agentic Learning Backend"}

# Debug endpoint to verify Gemini client initialization (enabled via env var)
@router.get("/debug/gen-init")
def debug_genie_init():
    """Attempt to lazily initialize the Gemini client and return a masked status.
    Enabled only if ENABLE_GENIE_DEBUG env var is set to '1'|'true'|'yes'.
    """
    enabled = os.getenv("ENABLE_GENIE_DEBUG", "false").lower() in ("1", "true", "yes")
    if not enabled:
        raise HTTPException(status_code=404, detail="Not Found")

    try:
        # Create a workflow instance and call the internal initializer
        wf = AgentWorkflow()
        # call protected internal method to trigger lazy init
        try:
            wf._ensure_model()
            # If we reach here, the client initialized
            gem_key = os.getenv("GEMINI_API_KEY")
            masked = (gem_key[:4] + "...." + gem_key[-4:]) if gem_key and len(gem_key) > 8 else ("****" if gem_key else None)
            return {"status": "initialized", "masked_key": masked}
        except Exception as e:
            # Return the error message but avoid leaking the key
            return {"status": "error", "message": str(e)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
