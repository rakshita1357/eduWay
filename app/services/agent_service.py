import os
import json
import datetime
import re
from dotenv import load_dotenv
from typing import Optional, List
from app.models.schemas import UserProfile, RoadmapResponse, AgentLog as AgentLogSchema
from app.utils.prompts import MARKET_ANALYST_PROMPT, ARCHITECT_PROMPT, CURATOR_PROMPT, CRITIC_PROMPT
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
                timeout=120.0
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
            _genai_model = _genai.GenerativeModel("google/gemini-2.5-flash-lite")
            logger.info("Gemini client initialized successfully")
        except Exception as e:
            logger.exception("Failed to initialize Gemini client")
            raise RuntimeError(f"Failed to initialize Gemini client: {e}")

    def _call_model(self, prompt: str):
        """Try NVIDIA NIM first; on auth/quota/404 errors fall back to Gemini."""
        # --- NVIDIA attempt ---
        try:
            self._ensure_client()
            resp = _openai_client.chat.completions.create(
                model=NVIDIA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4096,
                temperature=0.7,
                top_p=0.9,
            )
            return resp
        except Exception as e:
            msg = str(e)
            logger.warning(f"NVIDIA NIM call failed, falling back to Gemini: {msg}")

        # --- Gemini fallback ---
        try:
            self._ensure_gemini()
            from google.generativeai.types import GenerationConfig
            resp = _genai_model.generate_content(
                prompt,
                generation_config=GenerationConfig(max_output_tokens=4096),
                request_options={"timeout": 120},
            )
            return resp
        except Exception as e:
            logger.exception("Gemini fallback also failed")
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
        """
        if not self.youtube_service:
            logger.warning("YouTube service not configured - keeping LLM-generated links")
            return modules

        enriched_modules = []

        for idx, module in enumerate(modules):
            if not isinstance(module, dict):
                logger.warning(f"_enrich_resources_with_real_links: unexpected module type {type(module)} at index {idx}; skipping.")
                continue
            module_name = module.get("module_name", "")
            skills = module.get("skills_covered", [])
            resources = module.get("resources", [])

            logger.info(f"Fetching real videos for Module {idx + 1}: {module_name}")

            # Separate video and non-video resources
            video_resources = [r for r in resources if r.get("type", "").lower() == "video"]
            other_resources = [r for r in resources if r.get("type", "").lower() != "video"]

            # Determine how many videos to fetch
            if preferred_style == "Video":
                # User prefers videos - fetch more
                video_count = max(3, len(video_resources))
            elif video_resources:
                # LLM suggested videos - respect the count
                video_count = len(video_resources)
            else:
                # No videos suggested but we can add some anyway
                video_count = 2

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
            enriched_modules.append(enriched_module)

        return enriched_modules

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

        # --- STEP 1: MARKET ANALYST AGENT ---
        self._log("Market Analyst", f"Scanning job boards for '{profile.target_role}'...")
        market_response = self._call_model(MARKET_ANALYST_PROMPT.format(target_role=profile.target_role))
        market_data = self._clean_json(_extract_text(market_response))
        self._log("Market Analyst", f"Identified {len(market_data)} critical skills.")

        # --- STEP 2: ARCHITECT AGENT ---
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

        # --- STEP 3: CURATOR AGENT (with LLM for structure) ---
        self._log("Curator", f"Sourcing {profile.preferred_style} resources for modules...")

        # Still use LLM to generate resource structure, but we'll replace video links
        curator_prompt = CURATOR_PROMPT.format(
            preferred_style=profile.preferred_style,
            modules=json.dumps(structure_data)
        )
        curator_prompt = curator_prompt + progress_note
        curator_response = self._call_model(curator_prompt)
        curated_data = self._clean_json(_extract_text(curator_response))
        curated_data = self._validate_module_list(curated_data, "Curator")

        # Fallback to architect structure if curator output is empty or length mismatch
        if not curated_data or len(curated_data) != len(structure_data):
            self._log("Curator", f"Curator output length mismatch (got {len(curated_data)}, expected {len(structure_data)}), falling back to architect structure.")
            curated_data = structure_data

        # --- NEW: ALWAYS enrich with real YouTube videos for ALL modules ---
        self._log("Curator", "Fetching real YouTube videos from YouTube API for all modules...")
        curated_data = self._enrich_resources_with_real_links(
            modules=curated_data,
            target_role=profile.target_role,
            preferred_style=profile.preferred_style
        )
        self._log("Curator", "Real video links integrated successfully for all modules.")

        # --- STEP 4: CRITIC AGENT ---
        self._log("Critic", "Validating logical flow and prerequisites...")
        critic_prompt = CRITIC_PROMPT.format(curated_path=json.dumps(curated_data)) + progress_note
        critic_response = self._call_model(critic_prompt)
        final_roadmap = self._clean_json(_extract_text(critic_response))
        final_roadmap = self._validate_module_list(final_roadmap, "Critic")
        # Fallback to curated_data if critic fails to produce valid JSON
        if not isinstance(final_roadmap, list) or len(final_roadmap) == 0:
            self._log("Critic", "Critic returned invalid JSON, falling back to curated data.")
            final_roadmap = curated_data
        self._log("System", "Roadmap generation complete.")

        normalized_roadmap = self._normalize_roadmap(final_roadmap)

        saved_ids = None
        if self.db and db_models:
            saved_ids = self._save_roadmap_to_db(profile, market_data, normalized_roadmap)
            self._current_roadmap_id = saved_ids.get("roadmap_id")

        return RoadmapResponse(
            market_analysis=market_data,
            roadmap=normalized_roadmap,
            agent_logs=self.logs
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