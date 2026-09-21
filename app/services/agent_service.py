import os
import json
import datetime
import re
import time
from dotenv import load_dotenv
from typing import Optional, List
from concurrent.futures import ThreadPoolExecutor, as_completed
from app.models.schemas import UserProfile, RoadmapResponse, AgentLog as AgentLogSchema, GapAnalysis
from app.utils.prompts import (
    MARKET_ANALYST_PROMPT,
    ARCHITECT_PROMPT,
    CURATOR_PROMPT,
    CRITIC_PROMPT,
    GAP_ANALYST_PROMPT,
    ARCHITECT_FROM_GAP_PROMPT,
)
import logging

# Import the YouTube search service
from app.services.youtube_service import YouTubeSearchService, SerperSearchService

load_dotenv()
logger = logging.getLogger("uvicorn.error")

# NVIDIA NIM API configuration
NVIDIA_API_KEY = os.getenv("NVIDIA_NIM_API") or os.getenv("NVIDIA_API_KEY")
if NVIDIA_API_KEY:
    NVIDIA_API_KEY = NVIDIA_API_KEY.strip().strip('"').strip("'")

NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")

_openai_client = None

# Gemini fallback configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    GEMINI_API_KEY = GEMINI_API_KEY.strip().strip('"').strip("'")

_genai = None
_genai_model = None

try:
    from app.db import models as db_models
    from sqlalchemy.orm import Session
except Exception:
    db_models = None
    Session = None


class AgentWorkflow:
    def __init__(self, db_session: Optional[Session] = None):
        self.logs = []
        self.db = db_session
        self._current_roadmap_id: Optional[int] = None

        # Initialize YouTube search service (try YouTube API first, fallback to Serper)
        self.youtube_service = None
        if os.getenv("YOUTUBE_API_KEY"):
            self.youtube_service = YouTubeSearchService()
            logger.info("Using YouTube Data API for video search")
        elif os.getenv("SERPER_API_KEY"):
            self.youtube_service = SerperSearchService()
            logger.info("Using Serper API for video search")
        else:
            logger.warning("No YouTube or Serper API key configured - will use LLM-generated links")

    def _ensure_client(self):
        """Lazily import and configure the OpenAI-compatible client for NVIDIA NIM."""
        global _openai_client, NVIDIA_API_KEY
        if _openai_client is not None:
            return
        if not NVIDIA_API_KEY:
            NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
            if NVIDIA_API_KEY:
                NVIDIA_API_KEY = NVIDIA_API_KEY.strip().strip('"').strip("'")
        if not NVIDIA_API_KEY:
            logger.warning("NVIDIA_API_KEY missing at client initialization time")
            raise RuntimeError(
                "NVIDIA API key is not configured. Set NVIDIA_API_KEY in your environment or .env before calling generation endpoints.")
        try:
            masked = (NVIDIA_API_KEY[:4] + "...." + NVIDIA_API_KEY[-4:]) if len(NVIDIA_API_KEY) > 8 else "****"
            logger.info(f"Initializing NVIDIA NIM client with key (masked): {masked}")
            logger.info(f"Using base URL: {NVIDIA_BASE_URL}")
            logger.info(f"Using model: {NVIDIA_MODEL}")

            from openai import OpenAI
            _openai_client = OpenAI(
                base_url=NVIDIA_BASE_URL,
                api_key=NVIDIA_API_KEY,
                timeout=180.0
            )
            logger.info("NVIDIA NIM client initialized successfully")
        except Exception as e:
            logger.exception("Failed to initialize NVIDIA NIM client")
            raise RuntimeError(f"Failed to initialize NVIDIA NIM client: {e}")

    def _ensure_gemini(self):
        """Lazily import and configure the Google Generative AI client."""
        global _genai, _genai_model, GEMINI_API_KEY
        if _genai_model is not None:
            return
        if not GEMINI_API_KEY:
            GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
            if GEMINI_API_KEY:
                GEMINI_API_KEY = GEMINI_API_KEY.strip().strip('"').strip("'")
        if not GEMINI_API_KEY:
            logger.warning("GEMINI_API_KEY missing at client initialization time")
            raise RuntimeError(
                "Gemini API key is not configured. Set GEMINI_API_KEY in your environment or .env before calling generation endpoints.")
        try:
            masked = (GEMINI_API_KEY[:4] + "...." + GEMINI_API_KEY[-4:]) if len(GEMINI_API_KEY) > 8 else "****"
            logger.info(f"Initializing Gemini client with key (masked): {masked}")

            import google.generativeai as genai
            _genai = genai
            _genai.configure(api_key=GEMINI_API_KEY)
            # NOTE: the google-generativeai SDK wants a bare model name (or
            # "models/<name>"), NOT an OpenRouter-style "google/<name>"
            # prefix. The old value caused every fallback call to throw
            # InvalidArgument immediately, meaning NVIDIA timeouts had no
            # working fallback at all.
            _genai_model = _genai.GenerativeModel("gemini-2.5-flash-lite")
            logger.info("Gemini client initialized successfully")
        except Exception as e:
            logger.exception("Failed to initialize Gemini client")
            raise RuntimeError(f"Failed to initialize Gemini client: {e}")

    def _call_model(self, prompt: str, max_tokens: int = 4096):
        """Try NVIDIA NIM first; on auth/quota/404 errors fall back to Gemini.

        max_tokens is now configurable per-call so steps that need to echo
        back large payloads can request more headroom instead of silently
        truncating at the old hardcoded 4096 limit.

        Also logs wall-clock time for each attempt so slow steps are
        visible in the server log instead of having to guess where time
        went.
        """
        # --- NVIDIA attempt ---
        t0 = time.time()
        try:
            self._ensure_client()
            resp = _openai_client.chat.completions.create(
                model=NVIDIA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0.7,
                top_p=0.9,
            )
            logger.info(f"[TIMING] NVIDIA call took {time.time() - t0:.1f}s")
            return resp
        except Exception as e:
            msg = str(e)
            logger.warning(f"[TIMING] NVIDIA call failed after {time.time() - t0:.1f}s, falling back to Gemini: {msg}")

        # --- Gemini fallback ---
        t1 = time.time()
        try:
            self._ensure_gemini()
            from google.generativeai.types import GenerationConfig
            resp = _genai_model.generate_content(
                prompt,
                generation_config=GenerationConfig(max_output_tokens=max_tokens),
                request_options={"timeout": 120},
            )
            logger.info(f"[TIMING] Gemini fallback call took {time.time() - t1:.1f}s")
            return resp
        except Exception as e:
            logger.exception(f"Gemini fallback also failed after {time.time() - t1:.1f}s")
            raise RuntimeError(f"Both NVIDIA and Gemini failed: {e}")

    def _log(self, agent: str, action: str):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.logs.append(AgentLogSchema(agent_name=agent, action=action, timestamp=timestamp))
        if self.db and db_models and getattr(self, "_current_roadmap_id", None):
            db_log = db_models.AgentLog(roadmap_id=self._current_roadmap_id, agent_name=agent, action=action,
                                        timestamp=timestamp)
            self.db.add(db_log)
            self.db.commit()
            self.db.refresh(db_log)

    def _fetch_real_youtube_videos(
            self,
            module_name: str,
            skills: List[str],
            target_role: str,
            count: int = 3
    ) -> List[dict]:
        """
        Fetch real YouTube videos for a module using the YouTube search service.
        Falls back to LLM-generated suggestions if service unavailable.
        """
        if not self.youtube_service:
            logger.warning("YouTube service not available, will use LLM suggestions")
            return []

        try:
            videos = self.youtube_service.search_for_module(
                module_name=module_name,
                skills=skills,
                target_role=target_role,
                count=count
            )

            if videos:
                logger.info(f"Found {len(videos)} real YouTube videos for '{module_name}'")
            else:
                logger.warning(f"No YouTube videos found for '{module_name}'")

            return videos

        except Exception as e:
            logger.error(f"Error fetching YouTube videos: {e}")
            return []

    def _strip_resources_for_critic(self, modules: List[dict]) -> List[dict]:
        """
        The Critic's job is to validate module ordering, prerequisites and
        logical flow — not to re-review every YouTube URL/duration/reason.
        Sending it the full resource lists (5 modules x ~3 enriched videos
        each) was the main driver of both the truncation bug and the
        request timeout: a much bigger prompt in, and the model has to
        echo most of it back out. Strip resources down to nothing here;
        _merge_critic_output_with_resources reattaches the real ones after.
        """
        light = []
        for m in modules:
            if not isinstance(m, dict):
                continue
            light.append({
                "module_name": m.get("module_name") or m.get("title"),
                "description": m.get("description", ""),
                "skills_covered": m.get("skills_covered", []),
                "why_needed": m.get("why_needed", ""),
                "estimated_time": m.get("estimated_time", ""),
            })
        return light

    def _merge_critic_output_with_resources(self, critic_modules: List[dict], curated_data: List[dict]) -> List[dict]:
        """
        Reattach each module's real (enriched) resources — stripped out
        before the Critic call — by matching on module_name against the
        pre-Critic curated_data. Preserves whatever ordering/edits the
        Critic made to the light module list.
        """
        by_name = {}
        for m in curated_data:
            if isinstance(m, dict):
                name = m.get("module_name") or m.get("title")
                if name:
                    by_name[name] = m

        merged = []
        for cm in critic_modules:
            if not isinstance(cm, dict):
                continue
            name = cm.get("module_name") or cm.get("title")
            original = by_name.get(name)
            resources = original.get("resources", []) if original else []
            merged.append({**cm, "resources": resources})
        return merged

    def _validate_module_list(self, data, step_name):
        """Ensure data is a list of dicts with required keys."""
        # If data is a dict that looks like a single module, wrap it in a list
        if isinstance(data, dict):
            if "module_name" in data or "title" in data:
                logger.info(f"{step_name}: wrapped single module dict into list")
                return [data]
            # If data is a dict, try to extract a list from common keys
            for key in ("modules", "roadmap", "learning_path", "data", "items"):
                if key in data and isinstance(data[key], list):
                    logger.info(f"{step_name}: extracted module list from key '{key}'")
                    data = data[key]
                    break
            else:
                # If dict has a single key that is a list, use that
                list_vals = [v for v in data.values() if isinstance(v, list)]
                if len(list_vals) == 1:
                    logger.info(f"{step_name}: extracted module list from sole list value")
                    data = list_vals[0]
                else:
                    logger.warning(f"{step_name}: expected list, got dict with keys {list(data.keys())}")
                    return []
        if not isinstance(data, list):
            logger.warning(f"{step_name}: expected list, got {type(data)}")
            return []
        validated = []
        for idx, item in enumerate(data):
            if not isinstance(item, dict):
                logger.warning(f"{step_name}: item {idx} is not a dict ({type(item)}), skipping")
                continue
            # Ensure required keys exist
            if "module_name" not in item and "title" not in item:
                logger.warning(f"{step_name}: item {idx} missing module_name/title, skipping")
                continue
            validated.append(item)
        if len(validated) != len(data):
            logger.warning(f"{step_name}: filtered {len(data) - len(validated)} invalid items")
        return validated

    def _enrich_resources_with_real_links(
            self,
            modules: List[dict],
            target_role: str,
            preferred_style: str
    ) -> List[dict]:
        """
        Replace ALL dummy/LLM-generated links with real YouTube videos.
        Fetches high-quality videos for every module.

        Runs the per-module YouTube/Serper lookups concurrently via a
        thread pool instead of a sequential for-loop, since
        _fetch_real_youtube_videos is a blocking/sync network call.
        This is the main latency win: 5 sequential lookups (~2-3 min)
        collapse to roughly the time of the single slowest lookup.
        """
        if not self.youtube_service:
            logger.warning("YouTube service not configured - keeping LLM-generated links")
            return modules

        def _determine_video_count(resources):
            video_resources = [r for r in resources if r.get("type", "").lower() == "video"]
            if preferred_style == "Video":
                # User prefers videos - fetch more
                return max(3, len(video_resources))
            elif video_resources:
                # LLM suggested videos - respect the count
                return len(video_resources)
            else:
                # No videos suggested but we can add some anyway
                return 2

        def process_one(idx, module):
            if not isinstance(module, dict):
                logger.warning(
                    f"_enrich_resources_with_real_links: unexpected module type {type(module)} at index {idx}; skipping."
                )
                return idx, module

            module_name = module.get("module_name", "")
            skills = module.get("skills_covered", [])
            resources = module.get("resources", [])

            logger.info(f"Fetching real videos for Module {idx + 1}: {module_name}")

            # Separate video and non-video resources
            video_resources = [r for r in resources if r.get("type", "").lower() == "video"]
            other_resources = [r for r in resources if r.get("type", "").lower() != "video"]

            video_count = _determine_video_count(resources)

            # ALWAYS fetch real YouTube videos for each module
            real_videos = self._fetch_real_youtube_videos(
                module_name=module_name,
                skills=skills,
                target_role=target_role,
                count=video_count
            )

            if real_videos:
                logger.info(f"✓ Found {len(real_videos)} real videos for Module {idx + 1}")
                # Combine real videos with other resource types
                new_resources = real_videos + other_resources
            else:
                logger.warning(f"✗ No real videos found for Module {idx + 1}, keeping original resources")
                new_resources = resources

            enriched_module = {**module, "resources": new_resources}
            return idx, enriched_module

        enriched_modules: List[Optional[dict]] = [None] * len(modules)
        max_workers = min(8, max(1, len(modules)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(process_one, idx, module) for idx, module in enumerate(modules)]
            for future in as_completed(futures):
                idx, result = future.result()
                enriched_modules[idx] = result

        # Filter out any None slots defensively (shouldn't happen, but keeps
        # this function's output contract identical to the old version).
        return [m for m in enriched_modules if m is not None]

    # ------------------------------------------------------------------
    # EXISTING FLOW: manual UserProfile -> Market Analyst -> Architect -> ...
    # ------------------------------------------------------------------
    async def generate_learning_path(self, profile: UserProfile,
                                     completed_module_ids: Optional[List[int]] = None) -> RoadmapResponse:

        progress_note = ""
        if completed_module_ids:
            progress_note = f"\n\nNOTE: The learner has completed modules with ids: {completed_module_ids}. When creating the updated path, skip or adapt content for those completed modules."

        def _extract_text(response):
            """Extract text from OpenAI-compatible response."""
            try:
                return response.choices[0].message.content
            except Exception:
                return str(response)

        pipeline_start = time.time()

        # --- STEP 1: MARKET ANALYST AGENT ---
        step_t0 = time.time()
        self._log("Market Analyst", f"Scanning job boards for '{profile.target_role}'...")
        market_response = self._call_model(MARKET_ANALYST_PROMPT.format(target_role=profile.target_role))
        market_data = self._clean_json(_extract_text(market_response))
        self._log("Market Analyst", f"Identified {len(market_data)} critical skills.")
        logger.info(f"[TIMING] Market Analyst step total: {time.time() - step_t0:.1f}s")

        # --- STEP 2: ARCHITECT AGENT ---
        step_t0 = time.time()
        self._log("Architect", "Designing curriculum structure based on gap analysis...")
        architect_prompt = ARCHITECT_PROMPT.format(
            current_skills=profile.current_skills,
            target_role=profile.target_role,
            market_trends=json.dumps(market_data)
        ) + progress_note
        architect_response = self._call_model(architect_prompt)
        structure_data = self._clean_json(_extract_text(architect_response))
        structure_data = self._validate_module_list(structure_data, "Architect")
        self._log("Architect", f"Created {len(structure_data)} modules.")
        logger.info(f"[TIMING] Architect step total: {time.time() - step_t0:.1f}s")

        # --- STEP 3: CURATOR AGENT (with LLM for structure) ---
        step_t0 = time.time()
        self._log("Curator", f"Sourcing {profile.preferred_style} resources for modules...")

        # Still use LLM to generate resource structure, but we'll replace video links
        curator_prompt = CURATOR_PROMPT.format(
            preferred_style=profile.preferred_style,
            modules=json.dumps(structure_data)
        )
        curator_prompt = curator_prompt + progress_note
        # Explicit count constraint, independent of whatever CURATOR_PROMPT
        # itself says — models were non-compliant here (returning 1 module
        # instead of N) even before the token-budget fix below, so this is
        # a belt-and-suspenders addition, not a replacement for it.
        curator_prompt += (
            f"\n\nIMPORTANT: Your response MUST contain exactly "
            f"{len(structure_data)} module objects — one for every module "
            f"listed in the input. Do not omit, merge, or drop any module."
        )
        # Curator has to emit a full JSON structure for every module,
        # including resource-stub objects — genuinely large output. Left at
        # the 4096 default this was truncating exactly like the old Critic
        # bug: _clean_json grabs the first complete module, gets wrapped
        # into a 1-item list, length-mismatch fallback kicks in and uses
        # bare Architect structure (which has NO resources field at all).
        # That's the direct cause of modules showing 0 resources.
        curator_response = self._call_model(curator_prompt, max_tokens=8192)
        curated_data = self._clean_json(_extract_text(curator_response))
        curated_data = self._validate_module_list(curated_data, "Curator")

        # Fallback to architect structure if curator output is empty or length mismatch
        if not curated_data or len(curated_data) != len(structure_data):
            self._log("Curator", f"Curator output length mismatch (got {len(curated_data)}, expected {len(structure_data)}), falling back to architect structure.")
            curated_data = structure_data
        logger.info(f"[TIMING] Curator LLM call step total: {time.time() - step_t0:.1f}s")

        # --- ALWAYS enrich with real YouTube videos for ALL modules ---
        step_t0 = time.time()
        self._log("Curator", "Fetching real YouTube videos from YouTube API for all modules...")
        curated_data = self._enrich_resources_with_real_links(
            modules=curated_data,
            target_role=profile.target_role,
            preferred_style=profile.preferred_style
        )
        self._log("Curator", "Real video links integrated successfully for all modules.")
        logger.info(f"[TIMING] YouTube enrichment step total: {time.time() - step_t0:.1f}s")

        # --- STEP 4: CRITIC AGENT ---
        step_t0 = time.time()
        self._log("Critic", "Validating logical flow and prerequisites...")
        # Strip resources before sending to the Critic — it only needs to
        # judge ordering/prerequisites, not re-review every video URL.
        # This keeps the prompt AND required output small, which is what
        # was causing both the truncation (silent 1-module bug) and the
        # NVIDIA request timeout you just hit.
        light_modules = self._strip_resources_for_critic(curated_data)
        critic_prompt = CRITIC_PROMPT.format(curated_path=json.dumps(light_modules)) + progress_note
        # Critic was still returning only 1 module even after resources
        # were stripped from its payload (so this wasn't a token-budget
        # issue) — add the same explicit count constraint as Curator.
        critic_prompt += (
            f"\n\nIMPORTANT: Your response MUST contain exactly "
            f"{len(light_modules)} module objects — one for every module "
            f"listed above, in the same set. Do not omit, merge, or drop "
            f"any module."
        )
        critic_response = self._call_model(critic_prompt, max_tokens=4096)
        critic_modules = self._clean_json(_extract_text(critic_response))
        critic_modules = self._validate_module_list(critic_modules, "Critic")

        if not isinstance(critic_modules, list) or len(critic_modules) != len(curated_data):
            self._log(
                "Critic",
                f"Critic output invalid or length mismatch (got "
                f"{len(critic_modules) if isinstance(critic_modules, list) else 'N/A'}, "
                f"expected {len(curated_data)}), falling back to curated data."
            )
            final_roadmap = curated_data
        else:
            final_roadmap = self._merge_critic_output_with_resources(critic_modules, curated_data)
        logger.info(f"[TIMING] Critic step total: {time.time() - step_t0:.1f}s")

        self._log("System", "Roadmap generation complete.")
        logger.info(f"[TIMING] Full pipeline total: {time.time() - pipeline_start:.1f}s")

        normalized_roadmap = self._normalize_roadmap(final_roadmap)

        saved_ids = None
        if self.db and db_models:
            saved_ids = self._save_roadmap_to_db(profile, market_data, normalized_roadmap)
            self._current_roadmap_id = saved_ids.get("roadmap_id")

        return RoadmapResponse(
            market_analysis=market_data,
            roadmap=normalized_roadmap,
            agent_logs=self.logs,
            roadmap_id=self._current_roadmap_id,
        )

    def generate_learning_path_sync(self, profile: UserProfile,
                                    completed_module_ids: Optional[List[int]] = None) -> dict:
        """Synchronous wrapper"""
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(
            self.generate_learning_path(profile, completed_module_ids=completed_module_ids))
        roadmap_id = getattr(self, "_current_roadmap_id", None)
        return {"roadmap_id": roadmap_id, "conversation_id": roadmap_id}

    def _save_roadmap_to_db(self, profile: UserProfile, market_analysis, roadmap):
        """Persist roadmap to database"""
        if not self.db or not db_models:
            return {}

        user = db_models.User(name=profile.name)
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)

        roadmap_row = db_models.Roadmap(
            user_id=user.id,
            target_role=profile.target_role,
            market_analysis=json.dumps(market_analysis),
            profile=json.dumps(profile.dict())
        )
        self.db.add(roadmap_row)
        self.db.commit()
        self.db.refresh(roadmap_row)

        for m in roadmap:
            module_row = db_models.Module(
                roadmap_id=roadmap_row.id,
                module_index=m.get("id", 0),
                module_name=m.get("module_name", ""),
                description=m.get("description", ""),
                skills_covered=json.dumps(m.get("skills_covered", [])),
                why_needed=m.get("why_needed", ""),
                estimated_time=m.get("estimated_time", "")
            )
            self.db.add(module_row)
            self.db.commit()
            self.db.refresh(module_row)

            for r in m.get("resources", []):
                resource_row = db_models.Resource(
                    module_id=module_row.id,
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    type=r.get("type", "Article"),
                    duration=r.get("duration", ""),
                    reason=r.get("reason", "")
                )
                self.db.add(resource_row)
            self.db.commit()

        for log in self.logs:
            log_row = db_models.AgentLog(
                roadmap_id=roadmap_row.id,
                agent_name=log.agent_name,
                action=log.action,
                timestamp=log.timestamp
            )
            self.db.add(log_row)
        self.db.commit()

        self._current_roadmap_id = roadmap_row.id
        return {"roadmap_id": roadmap_row.id}

    # ------------------------------------------------------------------
    # NEW FLOW: Resume + Job Description -> Gap Analyst -> Architect -> ...
    # ------------------------------------------------------------------
    async def generate_roadmap_from_gap_analysis(
        self,
        name: str,
        target_role: str,
        preferred_style: str,
        resume_text: str,
        jd_text: str,
        jd_source_url: Optional[str] = None,
        completed_module_ids: Optional[List[int]] = None,
    ) -> RoadmapResponse:
        """
        Resume/JD-driven variant of generate_learning_path. Steps 2-4
        (Curator -> YouTube enrichment -> Critic) are identical to the
        manual flow; only skill-gap discovery (Gap Analyst) and curriculum
        design (Architect, gap-driven prompt) differ.
        """

        progress_note = ""
        if completed_module_ids:
            progress_note = (
                f"\n\nNOTE: The learner has completed modules with ids: "
                f"{completed_module_ids}. Skip or adapt content for those."
            )

        def _extract_text(response):
            try:
                return response.choices[0].message.content
            except Exception:
                return str(response)

        pipeline_start = time.time()

        # --- STEP 0: GAP ANALYST AGENT ---
        step_t0 = time.time()
        self._log("Gap Analyst", "Comparing resume against job description...")
        gap_prompt = GAP_ANALYST_PROMPT.format(
            resume_text=resume_text,
            target_role=target_role,
            jd_text=jd_text,
        )
        gap_response = self._call_model(gap_prompt, max_tokens=4096)
        gap_data = self._clean_json(_extract_text(gap_response))
        if not isinstance(gap_data, dict):
            gap_data = {}
        gap_data.setdefault("matched_skills", [])
        gap_data.setdefault("gaps", [])
        try:
            gap_analysis = GapAnalysis(**gap_data)
        except Exception as e:
            logger.warning(f"Gap Analyst output failed validation, using empty gap analysis: {e}")
            gap_analysis = GapAnalysis()
        self._log(
            "Gap Analyst",
            f"Found {len(gap_analysis.matched_skills)} matched skills and "
            f"{len(gap_analysis.gaps)} gaps."
        )
        logger.info(f"[TIMING] Gap Analyst step total: {time.time() - step_t0:.1f}s")

        # --- STEP 1: ARCHITECT AGENT (gap-driven) ---
        step_t0 = time.time()
        self._log("Architect", "Designing curriculum to close identified gaps...")
        architect_prompt = ARCHITECT_FROM_GAP_PROMPT.format(
            target_role=target_role,
            matched_skills=json.dumps(gap_analysis.matched_skills),
            gaps=json.dumps([g.dict() for g in gap_analysis.gaps]),
        ) + progress_note
        architect_response = self._call_model(architect_prompt)
        structure_data = self._clean_json(_extract_text(architect_response))
        structure_data = self._validate_module_list(structure_data, "Architect")
        self._log("Architect", f"Created {len(structure_data)} modules.")
        logger.info(f"[TIMING] Architect step total: {time.time() - step_t0:.1f}s")

        # --- STEP 2: CURATOR AGENT (same as manual flow) ---
        step_t0 = time.time()
        self._log("Curator", f"Sourcing {preferred_style} resources for modules...")
        curator_prompt = CURATOR_PROMPT.format(
            preferred_style=preferred_style,
            modules=json.dumps(structure_data),
        ) + progress_note
        curator_prompt += (
            f"\n\nIMPORTANT: Your response MUST contain exactly "
            f"{len(structure_data)} module objects — one for every module "
            f"listed in the input. Do not omit, merge, or drop any module."
        )
        curator_response = self._call_model(curator_prompt, max_tokens=8192)
        curated_data = self._clean_json(_extract_text(curator_response))
        curated_data = self._validate_module_list(curated_data, "Curator")

        if not curated_data or len(curated_data) != len(structure_data):
            self._log("Curator", f"Curator output length mismatch (got {len(curated_data)}, expected {len(structure_data)}), falling back to architect structure.")
            curated_data = structure_data
        logger.info(f"[TIMING] Curator LLM call step total: {time.time() - step_t0:.1f}s")

        # --- ALWAYS enrich with real YouTube videos for ALL modules ---
        step_t0 = time.time()
        self._log("Curator", "Fetching real YouTube videos from YouTube API for all modules...")
        curated_data = self._enrich_resources_with_real_links(
            modules=curated_data,
            target_role=target_role,
            preferred_style=preferred_style,
        )
        self._log("Curator", "Real video links integrated successfully for all modules.")
        logger.info(f"[TIMING] YouTube enrichment step total: {time.time() - step_t0:.1f}s")

        # --- STEP 3: CRITIC AGENT (same as manual flow) ---
        step_t0 = time.time()
        self._log("Critic", "Validating logical flow and prerequisites...")
        light_modules = self._strip_resources_for_critic(curated_data)
        critic_prompt = CRITIC_PROMPT.format(curated_path=json.dumps(light_modules)) + progress_note
        critic_prompt += (
            f"\n\nIMPORTANT: Your response MUST contain exactly "
            f"{len(light_modules)} module objects — one for every module "
            f"listed above, in the same set. Do not omit, merge, or drop "
            f"any module."
        )
        critic_response = self._call_model(critic_prompt, max_tokens=4096)
        critic_modules = self._clean_json(_extract_text(critic_response))
        critic_modules = self._validate_module_list(critic_modules, "Critic")

        if not isinstance(critic_modules, list) or len(critic_modules) != len(curated_data):
            self._log(
                "Critic",
                f"Critic output invalid or length mismatch (got "
                f"{len(critic_modules) if isinstance(critic_modules, list) else 'N/A'}, "
                f"expected {len(curated_data)}), falling back to curated data."
            )
            final_roadmap = curated_data
        else:
            final_roadmap = self._merge_critic_output_with_resources(critic_modules, curated_data)
        logger.info(f"[TIMING] Critic step total: {time.time() - step_t0:.1f}s")

        self._log("System", "Roadmap generation complete.")
        logger.info(f"[TIMING] Full pipeline total: {time.time() - pipeline_start:.1f}s")

        normalized_roadmap = self._normalize_roadmap(final_roadmap)

        if self.db and db_models:
            self._save_gap_roadmap_to_db(
                name=name,
                target_role=target_role,
                preferred_style=preferred_style,
                resume_text=resume_text,
                jd_text=jd_text,
                jd_source_url=jd_source_url,
                gap_analysis=gap_analysis,
                roadmap=normalized_roadmap,
            )

        # Also surface gaps in the market_analysis field (shape-compatible
        # with MarketTrend) so any existing UI reading that field still
        # shows something meaningful for gap-based roadmaps.
        market_analysis_view = [
            {"skill": g.skill, "demand_level": g.importance, "growth_metric": g.status}
            for g in gap_analysis.gaps
        ]

        return RoadmapResponse(
            market_analysis=market_analysis_view,
            roadmap=normalized_roadmap,
            agent_logs=self.logs,
            gap_analysis=gap_analysis,
            roadmap_id=self._current_roadmap_id,
        )

    def generate_roadmap_from_gap_analysis_sync(self, *args, **kwargs) -> dict:
        """Synchronous wrapper, mirrors generate_learning_path_sync."""
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result: RoadmapResponse = loop.run_until_complete(
            self.generate_roadmap_from_gap_analysis(*args, **kwargs)
        )
        roadmap_id = getattr(self, "_current_roadmap_id", None)
        return {"roadmap_id": roadmap_id, "conversation_id": roadmap_id, "result": result}

    def _save_gap_roadmap_to_db(
        self,
        name: str,
        target_role: str,
        preferred_style: str,
        resume_text: str,
        jd_text: str,
        jd_source_url: Optional[str],
        gap_analysis: GapAnalysis,
        roadmap: list,
    ):
        """Persist a resume/JD-driven roadmap to the database."""
        if not self.db or not db_models:
            return {}

        user = db_models.User(name=name)
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)

        roadmap_row = db_models.Roadmap(
            user_id=user.id,
            target_role=target_role,
            market_analysis=json.dumps([g.dict() for g in gap_analysis.gaps]),
            profile=json.dumps({
                "name": name,
                "target_role": target_role,
                "preferred_style": preferred_style,
            }),
            resume_text=resume_text,
            jd_text=jd_text,
            jd_source_url=jd_source_url,
            gap_analysis=gap_analysis.json(),
        )
        self.db.add(roadmap_row)
        self.db.commit()
        self.db.refresh(roadmap_row)

        for m in roadmap:
            module_row = db_models.Module(
                roadmap_id=roadmap_row.id,
                module_index=m.get("id", 0),
                module_name=m.get("module_name", ""),
                description=m.get("description", ""),
                skills_covered=json.dumps(m.get("skills_covered", [])),
                why_needed=m.get("why_needed", ""),
                estimated_time=m.get("estimated_time", ""),
            )
            self.db.add(module_row)
            self.db.commit()
            self.db.refresh(module_row)

            for r in m.get("resources", []):
                resource_row = db_models.Resource(
                    module_id=module_row.id,
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    type=r.get("type", "Article"),
                    duration=r.get("duration", ""),
                    reason=r.get("reason", ""),
                )
                self.db.add(resource_row)
            self.db.commit()

        for log in self.logs:
            log_row = db_models.AgentLog(
                roadmap_id=roadmap_row.id,
                agent_name=log.agent_name,
                action=log.action,
                timestamp=log.timestamp,
            )
            self.db.add(log_row)
        self.db.commit()

        self._current_roadmap_id = roadmap_row.id
        return {"roadmap_id": roadmap_row.id}

    def _normalize_resource_type(self, raw_type: str) -> str:
        """Map resource type strings to allowed literals"""
        if not raw_type:
            return "Article"
        t = raw_type.lower()
        if "video" in t:
            return "Video"
        if "course" in t:
            return "Course"
        if "doc" in t or "documentation" in t or "docs" in t:
            return "Documentation"
        if "article" in t or "blog" in t or "post" in t:
            return "Article"
        return "Article"

    def _ensure_url(self, url: str) -> str:
        if not url or not isinstance(url, str) or not url.strip():
            return "https://example.com"
        return url.strip()

    def _normalize_roadmap(self, raw) -> list:
        """Convert model output into normalized modules"""
        modules = []
        if not raw:
            return []

        if isinstance(raw, dict):
            if "learning_path" in raw and isinstance(raw["learning_path"], list):
                modules = raw["learning_path"]
            elif "roadmap" in raw and isinstance(raw["roadmap"], list):
                modules = raw["roadmap"]
            else:
                if "module_name" in raw:
                    modules = [raw]
                else:
                    for v in raw.values():
                        if isinstance(v, list):
                            modules = v
                            break
        elif isinstance(raw, list):
            modules = raw

        normalized = []
        for idx, m in enumerate(modules):
            if not isinstance(m, dict):
                continue
            module_name = m.get("module_name") or m.get("title") or m.get("name") or f"Module {idx + 1}"
            description = m.get("description") or m.get("desc") or ""
            skills = m.get("skills_covered") or m.get("skills") or []
            why_needed = m.get("why_needed") or m.get("why") or ""
            estimated_time = m.get("estimated_time") or m.get("duration") or ""

            raw_resources = m.get("resources") or []
            resources = []
            for r in raw_resources:
                if not isinstance(r, dict):
                    continue
                title = r.get("title") or r.get("name") or "Untitled Resource"
                url = self._ensure_url(r.get("url") or r.get("link") or "")
                raw_type = r.get("type") or r.get("format") or ""
                rtype = self._normalize_resource_type(raw_type)
                duration = r.get("duration") or r.get("length") or ""
                reason = r.get("reason") or r.get("why") or ""
                resources.append({
                    "title": title,
                    "url": url,
                    "type": rtype,
                    "duration": duration,
                    "reason": reason
                })

            normalized.append({
                "id": idx + 1,
                "module_name": module_name,
                "description": description,
                "skills_covered": skills if isinstance(skills, list) else [skills],
                "resources": resources,
                "why_needed": why_needed,
                "estimated_time": estimated_time
            })

        return normalized

    def _extract_json_substring(self, text: str):
        """Find balanced JSON in text"""
        for start_idx, ch in enumerate(text):
            if ch not in '{[':
                continue
            stack = [ch]
            in_string = False
            escape = False
            for i in range(start_idx + 1, len(text)):
                c = text[i]
                if escape:
                    escape = False
                    continue
                if c == '\\':
                    escape = True
                    continue
                if c == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if c in '{[':
                    stack.append(c)
                elif c in '}]':
                    if not stack:
                        break
                    last = stack[-1]
                    if (last == '{' and c == '}') or (last == '[' and c == ']'):
                        stack.pop()
                        if not stack:
                            return text[start_idx:i + 1]
                    else:
                        break
        return None

    def _clean_json(self, text_response: str):
        """Extract and parse JSON from model response"""
        fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text_response, re.DOTALL | re.IGNORECASE)
        candidate = None
        if fenced:
            candidate = fenced.group(1).strip()
        else:
            candidate = self._extract_json_substring(text_response)

        if not candidate:
            print("Failed to parse JSON: no JSON-like substring found in response")
            return []

        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, (dict, list)):
                return parsed
            print(f"_clean_json: unexpected JSON type {type(parsed)}; expected dict or list. Returning empty list.")
            return []
        except json.JSONDecodeError:
            inner = self._extract_json_substring(candidate)
            if inner:
                try:
                    inner_parsed = json.loads(inner)
                    if isinstance(inner_parsed, (dict, list)):
                        return inner_parsed
                    print(f"_clean_json: unexpected inner JSON type {type(inner_parsed)}; expected dict or list. Returning empty list.")
                    return []
                except json.JSONDecodeError:
                    pass
            print(f"Failed to parse JSON: {candidate[:300]}")
            return []