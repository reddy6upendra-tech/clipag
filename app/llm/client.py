from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

class ClipCandidate(BaseModel):
    start: float = Field(..., description="Start timestamp in seconds")
    end: float = Field(..., description="End timestamp in seconds")
    title: str = Field(..., description="Engaging, punchy Reel/Short title in the clip's dominant language")
    hook_line: str = Field(..., description="The hook spoken in the first 3 seconds")
    reason: str = Field(..., description="Why this clip is viral, educational, or emotional")
    score: int = Field(..., ge=0, le=100, description="Virality and engagement score 0-100")
    suggested_hashtags: List[str] = Field(..., min_length=1, max_length=10, description="5 relevant hashtags for Reels/Shorts")
    description: str = Field(..., description="One-line summary for the caption")

class ClipSelectionResponse(BaseModel):
    clips: List[ClipCandidate] = Field(..., min_length=1, description="List of top self-contained viral clips")

class LLMProvider(ABC):
    @abstractmethod
    def find_clips(
        self,
        transcript_text: str,
        audio_hints: Dict[str, Any],
        target_clips: int = 6,
        min_duration: int = 20,
        max_duration: int = 60,
        progress_callback: Optional[Any] = None,
    ) -> List[ClipCandidate]:
        """Finds viral clips from transcript and audio hints."""
        pass
