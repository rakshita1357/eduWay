import os
import re
import requests
from typing import List, Dict, Optional
import logging

logger = logging.getLogger("uvicorn.error")


def _simplify_query_term(text: str, max_words: int = 6) -> str:
    """
    LLM-generated module names and skills often come back as full
    descriptive strings rather than short keywords, e.g.:
        "TypeScript 5.x: Strict Mode, Generics, Conditional Types,
         Mapped Types, Type Inference, Utility Types (Pick, Omit, ReturnType)"
    Joining a couple of those verbatim into a search query produces a wall
    of text that YouTube's search can't match against real video titles
    (this was the direct cause of most "No high-quality videos found"
    warnings). This strips it down to something searchable:
      - drop anything in parentheses (asides, examples)
      - keep only the part before the first colon (the "headline" term)
      - drop a leading "Module N:" prefix if present
      - cap to max_words words
    """
    if not text:
        return ""
    cleaned = re.sub(r"^Module\s*\d+\s*[:\-]\s*", "", text.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\([^)]*\)", "", cleaned)
    if ":" in cleaned:
        cleaned = cleaned.split(":", 1)[0]
    words = cleaned.strip().split()
    return " ".join(words[:max_words]).strip()


class YouTubeSearchService:
    """Service to fetch real, high-quality YouTube videos using YouTube Data API v3"""

    def __init__(self):
        self.api_key = os.getenv("YOUTUBE_API_KEY")
        self.base_url = "https://www.googleapis.com/youtube/v3/search"

    def search_videos(
            self,
            query: str,
            max_results: int = 5,
            duration: str = "medium",  # short, medium, long, any
            relevance_language: str = "en"  # Prefer English content
    ) -> List[Dict[str, str]]:
        """
        Search YouTube for high-quality videos matching the query.

        Args:
            query: Search term (e.g., "React hooks tutorial")
            max_results: Number of videos to return
            duration: Video duration filter
            relevance_language: Language preference (default: English)

        Returns:
            List of dicts with: title, video_id, url, duration, channel
        """
        if not self.api_key:
            logger.warning("YOUTUBE_API_KEY not configured, returning empty results")
            return []

        try:
            params = {
                "part": "snippet",
                "q": query,
                "type": "video",
                "maxResults": max_results * 2,  # Get more to filter later
                "videoDuration": duration,
                "order": "relevance",  # Most relevant first
                "relevanceLanguage": relevance_language,
                "key": self.api_key,
                "videoEmbeddable": "true",
                "videoSyndicated": "true",
                "safeSearch": "moderate"
            }

            response = requests.get(self.base_url, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()
            videos = []

            for item in data.get("items", []):
                video_id = item["id"].get("videoId")
                if not video_id:
                    continue

                snippet = item.get("snippet", {})
                videos.append({
                    "title": snippet.get("title", "Untitled Video"),
                    "video_id": video_id,
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "channel": snippet.get("channelTitle", "Unknown Channel"),
                    "description": snippet.get("description", "")[:150],
                    "thumbnail": snippet.get("thumbnails", {}).get("default", {}).get("url", "")
                })

            # Get detailed stats to filter by quality
            if videos:
                video_ids = [v["video_id"] for v in videos]
                details = self.get_video_details(video_ids)

                # Enrich and filter videos
                quality_videos = []
                for video in videos:
                    vid = video["video_id"]
                    if vid in details:
                        video.update(details[vid])
                        # Filter: must have at least 1000 views
                        if int(video.get("views", "0")) >= 1000:
                            quality_videos.append(video)

                # Sort by likes ratio and views (quality metric)
                quality_videos.sort(
                    key=lambda x: (
                        float(x.get("likes_ratio", 0)),
                        int(x.get("views", 0))
                    ),
                    reverse=True
                )

                return quality_videos[:max_results]

            return videos[:max_results]

        except requests.exceptions.RequestException as e:
            logger.error(f"YouTube API request failed: {e}")
            return []
        except Exception as e:
            logger.error(f"Error searching YouTube: {e}")
            return []

    def get_video_details(self, video_ids: List[str]) -> Dict[str, Dict]:
        """
        Get detailed information about specific videos including duration, views, likes.
        Returns quality metrics for filtering.

        Args:
            video_ids: List of YouTube video IDs

        Returns:
            Dict mapping video_id to details (duration, views, likes, likes_ratio)
        """
        if not self.api_key or not video_ids:
            return {}

        try:
            # YouTube API allows up to 50 IDs per request
            video_ids_str = ",".join(video_ids[:50])

            params = {
                "part": "contentDetails,statistics",
                "id": video_ids_str,
                "key": self.api_key
            }

            response = requests.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params=params,
                timeout=10
            )
            response.raise_for_status()

            data = response.json()
            details = {}

            for item in data.get("items", []):
                video_id = item.get("id")
                content = item.get("contentDetails", {})
                stats = item.get("statistics", {})

                duration = self._parse_duration(content.get("duration", ""))
                views = int(stats.get("viewCount", "0"))
                likes = int(stats.get("likeCount", "0"))

                # Calculate quality metrics
                likes_ratio = (likes / views * 100) if views > 0 else 0

                details[video_id] = {
                    "duration": duration,
                    "views": str(views),
                    "likes": str(likes),
                    "likes_ratio": likes_ratio  # Higher is better quality
                }

            return details

        except Exception as e:
            logger.error(f"Error fetching video details: {e}")
            return {}

    def _parse_duration(self, iso_duration: str) -> str:
        """Convert ISO 8601 duration to readable format (e.g., PT15M33S -> 15:33)"""
        if not iso_duration:
            return "Unknown"

        try:
            import re as _re
            pattern = r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?'
            match = _re.match(pattern, iso_duration)

            if not match:
                return "Unknown"

            hours, minutes, seconds = match.groups()
            hours = int(hours) if hours else 0
            minutes = int(minutes) if minutes else 0
            seconds = int(seconds) if seconds else 0

            if hours > 0:
                return f"{hours}h {minutes}m"
            elif minutes > 0:
                return f"{minutes}m {seconds}s"
            else:
                return f"{seconds}s"

        except Exception:
            return "Unknown"

    def search_for_module(
            self,
            module_name: str,
            skills: List[str],
            target_role: str,
            count: int = 3
    ) -> List[Dict[str, str]]:
        """
        Search for HIGH-QUALITY videos relevant to a specific module.
        Filters for English content with good engagement metrics.

        Args:
            module_name: Name of the learning module
            skills: Skills covered in this module
            target_role: User's target role for context
            count: Number of videos to return

        Returns:
            List of high-quality video resources
        """
        # Create focused search query
        # Priority: specific skills > module name > general role
        primary_skills_raw = skills[:2] if len(skills) >= 2 else skills
        # Sanitize each skill string down to a short searchable headline —
        # raw skill strings from the LLM are often full descriptive
        # paragraphs with parentheticals and colons, which produced
        # unmatchable queries (see _simplify_query_term docstring).
        primary_skills = [_simplify_query_term(s) for s in primary_skills_raw]
        primary_skills = [s for s in primary_skills if s]

        simple_module_name = _simplify_query_term(module_name, max_words=8)

        if primary_skills:
            # Focus on specific skills for better results
            query = f"{' '.join(primary_skills)} tutorial"
        elif simple_module_name:
            # Fallback to module name
            query = f"{simple_module_name} {target_role} tutorial"
        else:
            query = f"{target_role} tutorial"

        logger.info(f"Searching YouTube for: '{query}'")

        # Search for high-quality videos
        videos = self.search_videos(
            query,
            max_results=count,
            duration="medium",  # Prefer substantive tutorials (10-20 min)
            relevance_language="en"  # English content
        )

        if not videos:
            logger.warning(f"No high-quality videos found for: {query}")
            # Try a broader search — also uses the simplified module name,
            # not the raw verbose one, for the same reason as above.
            broader_name = simple_module_name or module_name
            broader_query = f"{broader_name} beginner tutorial"
            videos = self.search_videos(broader_query, max_results=count, relevance_language="en")
            if not videos:
                logger.warning(f"No videos found on broader search either: {broader_query}")

        # Format for response
        enriched = []
        for video in videos[:count]:
            skill_text = ", ".join(primary_skills) if primary_skills else (simple_module_name or module_name)
            enriched.append({
                "title": video["title"],
                "url": video["url"],
                "type": "Video",
                "duration": video.get("duration", "Unknown"),
                "reason": f"Top-rated tutorial for {skill_text} • {video.get('views', '0')} views • {video['channel']}",
                "channel": video["channel"],
                "views": video.get("views", "0"),
                "likes": video.get("likes", "0")
            })

        return enriched


class SerperSearchService:
    """Alternative service using Serper.dev API"""

    def __init__(self):
        self.api_key = os.getenv("SERPER_API_KEY")
        self.base_url = "https://google.serper.dev/search"

    def search_videos(self, query: str, max_results: int = 3) -> List[Dict[str, str]]:
        """Search for YouTube videos using Serper API"""
        if not self.api_key:
            logger.warning("SERPER_API_KEY not configured")
            return []

        try:
            headers = {
                "X-API-KEY": self.api_key,
                "Content-Type": "application/json"
            }

            # Add English and quality filters to query
            enhanced_query = f"{query} tutorial course site:youtube.com"

            payload = {
                "q": enhanced_query,
                "num": max_results * 2,  # Get more for filtering
                "type": "search",
                "gl": "us",  # Geographic location: US
                "hl": "en"  # Language: English
            }

            response = requests.post(
                self.base_url,
                json=payload,
                headers=headers,
                timeout=10
            )
            response.raise_for_status()

            data = response.json()
            videos = []

            for item in data.get("organic", []):
                link = item.get("link", "")
                if "youtube.com/watch" in link:
                    videos.append({
                        "title": item.get("title", "Untitled Video"),
                        "url": link,
                        "type": "Video",
                        "duration": "Varies",
                        "reason": item.get("snippet", "")[:100],
                        "channel": "YouTube"
                    })

            return videos[:max_results]

        except Exception as e:
            logger.error(f"Serper API request failed: {e}")
            return []

    def search_for_module(
            self,
            module_name: str,
            skills: List[str],
            target_role: str,
            count: int = 3
    ) -> List[Dict[str, str]]:
        """Search using Serper with quality filtering"""
        primary_skills_raw = skills[:2] if len(skills) >= 2 else skills
        primary_skills = [_simplify_query_term(s) for s in primary_skills_raw]
        primary_skills = [s for s in primary_skills if s]
        simple_module_name = _simplify_query_term(module_name, max_words=8)

        if primary_skills:
            query = f"{' '.join(primary_skills)} tutorial"
        elif simple_module_name:
            query = f"{simple_module_name} {target_role} tutorial"
        else:
            query = f"{target_role} tutorial"

        return self.search_videos(query, max_results=count)