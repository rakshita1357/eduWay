from fastapi import APIRouter, HTTPException, status, Depends, UploadFile, File, Form
from sqlalchemy.orm import Session
from typing import Optional
from app.models.schemas import UserProfile, RoadmapResponse
from app.services.agent_service import AgentWorkflow
from app.services.document_parser_service import extract_resume_text, extract_jd_text_from_file
from app.services.job_fetcher_service import fetch_job_description
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


@router.post("/generate-roadmap-from-docs", response_model=RoadmapResponse, status_code=status.HTTP_200_OK)
async def generate_roadmap_from_docs(
    name: str = Form(...),
    target_role: str = Form(...),
    preferred_style: str = Form("Video"),
    job_url: Optional[str] = Form(None),
    jd_text: Optional[str] = Form(None),
    resume: UploadFile = File(...),
    jd_file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_session),
):
    """
    Resume + Job Description -> Gap Analyst -> dynamic, gap-driven learning
    roadmap.

    The job description can be supplied THREE ways — provide exactly one
    (if more than one is given, precedence is jd_text > jd_file > job_url,
    since more direct input is more reliable than something we have to
    parse or scrape ourselves):
    - jd_text: pasted job description text (most reliable)
    - jd_file: an uploaded PDF/DOCX/TXT of the job posting
    - job_url: a link to the job posting; we attempt to fetch and extract
      the JD from the page. Many job boards render via JS or block
      scraping, so this can fail — in that case we return a 422 asking
      the caller to paste the JD text or upload it as a file instead.
    """
    if not job_url and not jd_text and not jd_file:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide a job description via jd_text, jd_file, or job_url.",
        )

    try:
        resume_text = await extract_resume_text(resume)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to read resume file: {str(e)}",
        )

    final_jd_text: Optional[str] = None
    jd_source_url: Optional[str] = None

    if jd_text:
        final_jd_text = jd_text
    elif jd_file:
        try:
            final_jd_text = await extract_jd_text_from_file(jd_file)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Failed to read job description file: {str(e)}",
            )
    elif job_url:
        fetched = await fetch_job_description(job_url)
        if not fetched:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Could not fetch or parse that job URL (the page may "
                    "require JavaScript or block automated access). "
                    "Please paste the job description text or upload it as a file instead."
                ),
            )
        final_jd_text = fetched
        jd_source_url = job_url

    try:
        workflow = AgentWorkflow(db_session=db)
        result = await workflow.generate_roadmap_from_gap_analysis(
            name=name,
            target_role=target_role,
            preferred_style=preferred_style,
            resume_text=resume_text,
            jd_text=final_jd_text,
            jd_source_url=jd_source_url,
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent Swarm Failure: {str(e)}",
        )


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def start_session(profile: UserProfile, db: Session = Depends(get_session)):
    """Start a learning session linked to a user/roadmap. Returns the saved roadmap id and session id."""
    try:
        workflow = AgentWorkflow(db_session=db)
        result = workflow.generate_learning_path_sync(profile)
        # result is a RoadmapResponse (pydantic) but already saved to DB inside workflow
        return {"roadmap_id": result.get("roadmap_id"), "conversation_id": result.get("conversation_id")}
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
        prog = db_models.ModuleProgress(roadmap_id=session_id, module_id=module_id, status=status_val, completed_at=datetime.datetime.now(datetime.timezone.utc))
        db.add(prog)
        db.commit()
        db.refresh(prog)
        return {"progress_id": prog.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sessions/{session_id}/regenerate", response_model=RoadmapResponse)
def regenerate_roadmap(session_id: int, db: Session = Depends(get_session)):
    """
    Regenerate a roadmap based on existing session progress and original
    profile. Automatically detects whether the original roadmap was
    resume/JD-driven (has resume_text stored) or manual-profile-driven,
    and re-runs the matching pipeline so gap-based roadmaps stay dynamic
    as modules are completed.
    """
    try:
        # load existing roadmap
        roadmap = db.query(db_models.Roadmap).filter(db_models.Roadmap.id == session_id).first()
        if not roadmap:
            raise HTTPException(status_code=404, detail="Session/Roadmap not found")

        # collect completed module ids (shared by both flows)
        completed = db.query(db_models.ModuleProgress).filter(
            db_models.ModuleProgress.roadmap_id == session_id,
            db_models.ModuleProgress.status == 'completed'
        ).all()
        completed_module_ids = [c.module_id for c in completed]

        workflow = AgentWorkflow(db_session=db)

        is_gap_based = bool(getattr(roadmap, "resume_text", None) and getattr(roadmap, "jd_text", None))

        if is_gap_based:
            profile_data = json.loads(roadmap.profile) if roadmap.profile else {}
            result_dict = workflow.generate_roadmap_from_gap_analysis_sync(
                name=profile_data.get("name", "Candidate"),
                target_role=roadmap.target_role,
                preferred_style=profile_data.get("preferred_style", "Video"),
                resume_text=roadmap.resume_text,
                jd_text=roadmap.jd_text,
                jd_source_url=roadmap.jd_source_url,
                completed_module_ids=completed_module_ids,
            )
            new_roadmap_id = result_dict.get("roadmap_id")
        else:
            profile_json = roadmap.profile
            if not profile_json:
                raise HTTPException(status_code=400, detail="Original profile not available for regeneration")
            profile_data = json.loads(profile_json)
            profile = UserProfile(**profile_data)

            result = workflow.generate_learning_path_sync(profile, completed_module_ids=completed_module_ids)
            new_roadmap_id = result.get("roadmap_id")

        new_roadmap = db.query(db_models.Roadmap).filter(db_models.Roadmap.id == new_roadmap_id).first()
        if not new_roadmap:
            raise HTTPException(status_code=500, detail="Failed to create regenerated roadmap")

        # fetch market_analysis/gap_analysis, modules, and logs to assemble response
        market_analysis = json.loads(new_roadmap.market_analysis) if new_roadmap.market_analysis else []
        gap_analysis = None
        if getattr(new_roadmap, "gap_analysis", None):
            try:
                gap_analysis = json.loads(new_roadmap.gap_analysis)
            except (TypeError, ValueError):
                gap_analysis = None

        modules = []
        for m in db.query(db_models.Module).filter(db_models.Module.roadmap_id == new_roadmap.id).all():
            resources = []
            for r in db.query(db_models.Resource).filter(db_models.Resource.module_id == m.id).all():
                resources.append({"title": r.title, "url": r.url, "type": r.type, "duration": r.duration, "reason": r.reason})
            modules.append({"id": m.module_index, "module_name": m.module_name, "description": m.description, "skills_covered": json.loads(m.skills_covered), "resources": resources, "why_needed": m.why_needed, "estimated_time": m.estimated_time})

        logs = []
        for l in db.query(db_models.AgentLog).filter(db_models.AgentLog.roadmap_id == new_roadmap.id).all():
            logs.append({"agent_name": l.agent_name, "action": l.action, "timestamp": l.timestamp})

        return RoadmapResponse(
            market_analysis=market_analysis,
            roadmap=modules,
            agent_logs=logs,
            gap_analysis=gap_analysis,
            roadmap_id=new_roadmap.id,
        )

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