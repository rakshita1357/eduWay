from typing import List, Optional, Dict, Literal
from pydantic import BaseModel, Field

# --- Input Models (Request Body) ---

class UserProfile(BaseModel):
    name: str = Field(..., example="Alex")
    current_role: str = Field(..., example="Student")
    target_role: str = Field(..., example="React Developer")
    current_skills: List[str] = Field(..., example=["HTML", "CSS", "JavaScript Basics"])
    preferred_style: Literal["Video", "Text", "Interactive"] = Field(default="Video", description="Feature D: VARK Model")
    experience_level: str = Field(default="Beginner", example="Beginner")


class GenerateFromDocsRequest(BaseModel):
    """
    Not used directly as a FastAPI body model (the route takes multipart
    Form fields + an UploadFile since a resume file is involved), but kept
    here as the canonical shape of that request for reference / for any
    internal service functions that want a typed object instead of loose
    args.
    """
    name: str
    target_role: str
    preferred_style: Literal["Video", "Text", "Interactive"] = "Video"
    job_url: Optional[str] = None
    jd_text: Optional[str] = None


# --- Output Models (Response Body) ---

class LearningResource(BaseModel):
    title: str
    url: str
    type: Literal["Video", "Article", "Course", "Documentation"]
    duration: str
    reason: str  # Why this specific link was chosen (Curator Agent)

class RoadmapModule(BaseModel):
    id: int
    module_name: str
    description: str
    skills_covered: List[str]
    resources: List[LearningResource]
    why_needed: str = Field(..., description="Feature C: Explainable AI - Agent reasoning")
    estimated_time: str

class MarketTrend(BaseModel):
    skill: str
    demand_level: str  # High, Critical, Emerging
    growth_metric: str # e.g., "+15% YoY"

class AgentLog(BaseModel):
    agent_name: str
    action: str
    timestamp: str


# --- Gap Analysis Models (resume + JD mode) ---

class GapSkill(BaseModel):
    skill: str
    status: Literal["Missing", "Partial", "Met"]
    importance: Literal["Critical", "High", "Medium"]
    note: Optional[str] = None  # why it matters / where the resume falls short


class GapAnalysis(BaseModel):
    matched_skills: List[str] = []
    gaps: List[GapSkill] = []
    resume_summary: Optional[str] = None
    jd_summary: Optional[str] = None


class RoadmapResponse(BaseModel):
    market_analysis: List[MarketTrend]
    roadmap: List[RoadmapModule]
    agent_logs: List[AgentLog]
    gap_analysis: Optional[GapAnalysis] = None  # populated only for resume/JD-driven roadmaps
    roadmap_id: Optional[int] = None            # populated when saved to DB