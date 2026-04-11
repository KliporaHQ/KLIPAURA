from utils.logger import log
from config import OUTPUT_DIR


class WaveSpeedClient:
    """Mock client for video rendering service."""
    
    def __init__(self):
        self.api_key = "mock_key"
    
    def generate_video(self, script: str, assets: dict) -> str:
        """Mock video generation."""
        log("Mock WaveSpeed video generation started", "WAVESPEED")
        import time
        time.sleep(1)  # simulate work
        log("Mock WaveSpeed video generation completed", "WAVESPEED")
        return str(OUTPUT_DIR / "video.mp4")
    
    def check_status(self, job_id: str):
        return {"status": "completed"}

wavespeed_client = WaveSpeedClient()
