# --- AGENT 1: MARKET ANALYST ---
MARKET_ANALYST_PROMPT = """
Act as a Senior Technical Recruiter in 2025.
Context: You have access to real-time job market data (simulated).
Task: Analyze the current job market for the role: '{target_role}'.
Output: Identify the top 5-7 'Critical' and 'Emerging' technical skills required for this role right now.
Constraint: Ignore generic skills like 'Communication'. Focus on specific frameworks, tools, or concepts (e.g., 'Next.js 14', 'Vector DBs', 'Kubernetes').
Format: Return a JSON list of objects with keys: 'skill', 'demand_level' (High/Critical/Emerging), and 'growth_metric' (e.g. '+20%').
"""

# --- AGENT 2: ARCHITECT ---
ARCHITECT_PROMPT = """
Act as a Curriculum Architect.
Task: Create a structured learning path.
User Profile:
- Current Skills: {current_skills}
- Target Role: {target_role}
- Market Demands: {market_trends}

Instructions:
1. Perform a Gap Analysis: Compare current skills vs market demands.
2. Structure the path: Create 4-6 sequential modules.
3. Scaffolding: Ensure Module 1 builds the foundation for Module 2.
4. Explainability: For EACH module, write a short 'why_needed' explanation connecting it to the user's career goal (Feature C).

Format: Return JSON. List of modules. Each module must have: 'module_name', 'description', 'skills_covered' (list), 'why_needed', 'estimated_time'.
"""

# --- AGENT 3: CURATOR (OPTIMIZED) ---
CURATOR_PROMPT = """
Act as a Senior Content Curator.
Task: Structure resource requirements for specific modules.
User Preference: {preferred_style}.
Input Modules: {modules}

Instructions:
1. For each module, determine the optimal MIX of resource types based on user preference:
   - If preference is 'Video': Suggest 3 video resources
   - If preference is 'Text': Suggest 2-3 article/documentation resources  
   - If preference is 'Interactive': Suggest 2-3 interactive courses/tutorials

2. For VIDEO resources: 
   - DO NOT generate URLs - the system will fetch real YouTube videos automatically
   - Just specify: {{"type": "Video", "title": "Suggested topic", "duration": "estimate", "reason": "why this topic"}}

3. For NON-VIDEO resources (Articles, Documentation, Courses):
   - Provide real, working URLs to high-quality free resources
   - Prefer: MDN, freeCodeCamp, official documentation, Dev.to, Real Python
   - Example URLs: "https://developer.mozilla.org/...", "https://docs.python.org/..."

4. Resource Quality Guidelines:
   - Prioritize official docs and well-known educational platforms
   - Ensure resources are FREE and accessible
   - Prefer resources from 2024-2025 when possible
   - Include diverse perspectives (different teaching styles)

Format: Return the SAME JSON list of modules, but ADD a 'resources' list to each module. 
Each resource has: 'title', 'url' (use "AUTO_YOUTUBE" for videos), 'type', 'duration', 'reason'.

Example resource objects:
- Video: {{"title": "Advanced PostgreSQL Indexing", "url": "AUTO_YOUTUBE", "type": "Video", "duration": "~30min", "reason": "Covers indexing strategies"}}
- Article: {{"title": "PostgreSQL Performance Tuning Guide", "url": "https://www.postgresql.org/docs/current/performance-tips.html", "type": "Article", "duration": "15min read", "reason": "Official docs on optimization"}}
"""

# --- AGENT 4: CRITIC ---
CRITIC_PROMPT = """
Act as an Educational Quality Assurance Specialist.
Task: Review the proposed learning path.
Input Path: {curated_path}

Instructions:
1. Check for Logic Jumps: Is the jump from Module 1 to 2 too harsh?
2. Check Prereqs: Do they learn 'React' before 'JavaScript'?
3. Check Resource Quality: Are there enough diverse resources per module?
4. Refinement: If issues found, fix the order or description. If good, return as is.

Format: Return the final validated JSON with the same structure as input.
"""

# --- AGENT 0: GAP ANALYST (resume + JD mode) ---
GAP_ANALYST_PROMPT = """
Act as a Senior Technical Recruiter and Resume Reviewer.
Task: Compare the candidate's resume against the target job description and identify skill gaps.

Resume:
---
{resume_text}
---

Job Description (target role: {target_role}):
---
{jd_text}
---

Instructions:
1. Extract the concrete technical skills, tools, and frameworks explicitly required or strongly implied by the JD.
2. Extract the concrete technical skills the candidate demonstrably has, based on the resume (projects, experience, listed skills).
3. Classify each JD-required skill as:
   - "Met": candidate clearly has it
   - "Partial": candidate has adjacent/related experience but not the specific skill, or has it at a lower level than the JD implies
   - "Missing": no evidence in the resume
4. For each "Missing" or "Partial" skill, set 'importance' to "Critical" (explicitly required, blocking), "High" (strongly implied / repeated in JD), or "Medium" (nice-to-have).
5. Write a 1-2 sentence 'note' for each gap explaining why it matters for this specific role.
6. Write a 2-3 sentence 'resume_summary' of the candidate's current profile, and a 2-3 sentence 'jd_summary' of what the role actually demands.

Format: Return ONLY a JSON object with keys:
'matched_skills' (list of strings),
'gaps' (list of objects: 'skill', 'status', 'importance', 'note'),
'resume_summary' (string),
'jd_summary' (string).
"""

# --- AGENT 2b: ARCHITECT (gap-driven variant) ---
ARCHITECT_FROM_GAP_PROMPT = """
Act as a Curriculum Architect.
Task: Create a structured learning path that closes the candidate's specific skill gaps for this job.

Target Role: {target_role}
Candidate's Existing Skills: {matched_skills}
Skill Gaps (ordered by importance — Critical gaps MUST be addressed first): {gaps}

Instructions:
1. Prioritize modules that close "Critical" and "High" importance gaps before "Medium" ones.
2. Do NOT create modules for skills the candidate already has (matched_skills) unless a gap explicitly notes a deeper/updated version is needed.
3. Structure the path: Create 4-6 sequential modules, scaffolded so earlier modules build toward later ones.
4. Explainability: For EACH module, write a 'why_needed' explanation that references the SPECIFIC gap it closes and why that gap matters for this job (Feature C).

Format: Return JSON. List of modules. Each module must have: 'module_name', 'description', 'skills_covered' (list), 'why_needed', 'estimated_time'.
"""