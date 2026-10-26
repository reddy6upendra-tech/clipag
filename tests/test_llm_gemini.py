import json
import pytest
from unittest.mock import MagicMock, patch
from app.llm.client import ClipCandidate, ClipSelectionResponse
from app.llm.gemini import GeminiProvider, snap_to_word_boundaries, deduplicate_and_rank_clips
from app.pipeline.heuristics import find_clips_heuristic

@pytest.fixture
def mock_words():
    return [
        {"word": "Hello", "start": 0.05, "end": 0.45},
        {"word": "world", "start": 0.50, "end": 0.95},
        {"word": "here", "start": 1.00, "end": 1.30},
        {"word": "is", "start": 1.35, "end": 1.50},
        {"word": "a", "start": 1.55, "end": 1.65},
        {"word": "great", "start": 1.70, "end": 2.10},
        {"word": "tip.", "start": 2.15, "end": 2.80},
        {"word": "Always", "start": 19.80, "end": 20.30},
        {"word": "remember", "start": 20.35, "end": 20.90},
        {"word": "this.", "start": 20.95, "end": 21.50},
        {"word": "Ending", "start": 44.50, "end": 45.00},
        {"word": "now.", "start": 45.10, "end": 45.60},
    ]

@pytest.fixture
def sample_candidate():
    return ClipCandidate(
        start=0.1, # between 0.05 and 0.45
        end=45.4,  # between 45.10 and 45.60
        title="Epic Reel Title",
        hook_line="Hello world here is a great tip",
        reason="Super engaging opening line",
        score=95,
        suggested_hashtags=["#shorts", "#reels", "#viral", "#tech", "#tip"],
        description="Great advice for editors.",
    )

def test_snap_to_word_boundaries(sample_candidate, mock_words):
    snapped = snap_to_word_boundaries([sample_candidate], mock_words, min_duration=20, max_duration=60)
    assert len(snapped) == 1
    # 0.1 should snap to 0.05
    assert snapped[0].start == 0.05
    # 45.4 should snap to 45.60
    assert snapped[0].end == 45.60
    assert snapped[0].end - snapped[0].start >= 20.0

def test_deduplicate_and_rank_clips():
    c1 = ClipCandidate(start=0.0, end=30.0, title="C1", hook_line="H1", reason="R1", score=90, suggested_hashtags=["#a"], description="D1")
    c2 = ClipCandidate(start=10.0, end=40.0, title="C2 Overlap", hook_line="H2", reason="R2", score=95, suggested_hashtags=["#a"], description="D2")
    c3 = ClipCandidate(start=60.0, end=90.0, title="C3 Clean", hook_line="H3", reason="R3", score=85, suggested_hashtags=["#a"], description="D3")
    
    # c2 overlaps with c1, but c2 has score 95 > 90, so c2 should be kept, c1 dropped
    selected = deduplicate_and_rank_clips([c1, c2, c3], target_clips=2)
    assert len(selected) == 2
    titles = [x.title for x in selected]
    assert "C2 Overlap" in titles
    assert "C3 Clean" in titles
    assert "C1" not in titles

def test_gemini_valid_response(sample_candidate):
    provider = GeminiProvider()
    provider.api_key = "test_gemini_key"
    valid_json = json.dumps({"clips": [sample_candidate.model_dump()]})
    
    with patch.object(provider, "_call_gemini_api", return_value=valid_json) as mock_call:
        transcript_data = {
            "segments": [{"start": 0.0, "end": 45.0, "text": "Hello world"}],
            "words": [{"word": "Hello", "start": 0.0, "end": 1.0}, {"word": "world", "start": 1.0, "end": 45.0}],
            "duration": 60.0,
        }
        clips = provider.find_clips(transcript_data, target_clips=1)
        assert len(clips) >= 1
        assert clips[0].title == sample_candidate.title

def test_gemini_invalid_then_valid_retry(sample_candidate):
    provider = GeminiProvider()
    provider.api_key = "test_gemini_key"
    valid_json = json.dumps({"clips": [sample_candidate.model_dump()]})
    invalid_json = "NOT_JSON_AT_ALL"
    
    with patch.object(provider, "_call_gemini_api", side_effect=[invalid_json, valid_json]) as mock_call:
        transcript_data = {
            "segments": [{"start": 0.0, "end": 45.0, "text": "Hello world"}],
            "words": [{"word": "Hello", "start": 0.0, "end": 1.0}, {"word": "world", "start": 1.0, "end": 45.0}],
            "duration": 60.0,
        }
        clips = provider.find_clips(transcript_data, target_clips=1)
        assert mock_call.call_count == 2
        assert len(clips) >= 1
        assert clips[0].title == sample_candidate.title

def test_gemini_429_backoff_then_success(sample_candidate):
    provider = GeminiProvider()
    valid_json = json.dumps({"clips": [sample_candidate.model_dump()]})
    
    mock_response = MagicMock()
    mock_response.text = valid_json
    
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = [
        Exception("429 ResourceExhausted: rate limit exceeded"),
        mock_response
    ]
    
    with patch("google.genai.Client", return_value=mock_client), \
         patch("time.sleep", return_value=None): # speed up test
        raw = provider._call_gemini_api("test prompt", "gemini-2.5-flash")
        assert raw == valid_json
        assert mock_client.models.generate_content.call_count == 2

def test_heuristic_fallback_produces_at_least_5_clips():
    # Synthetic 200-second transcript with questions, answers, and natural sentence ends
    segments = []
    for i in range(20):
        t0 = i * 10.0
        t1 = t0 + 9.5
        text = f"This is segment number {i}. Why is this important? Because it works!"
        segments.append({"id": i, "start": t0, "end": t1, "text": text})
        
    transcript_data = {
        "duration": 200.0,
        "segments": segments,
        "words": [],
    }
    
    clips = find_clips_heuristic(transcript_data, target_clips=6, min_duration=20, max_duration=50)
    assert len(clips) >= 5
    for c in clips:
        assert c.score >= 50
        assert c.end - c.start >= 20.0
        assert len(c.suggested_hashtags) >= 1
        assert c.title
        assert c.reason
