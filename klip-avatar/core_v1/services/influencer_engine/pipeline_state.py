"""
Influencer Engine — Pipeline State Machine (cognitive hardening).

Strict order: SCRIPT -> VOICE -> VIDEO -> THUMBNAIL.
No step can be skipped or run out of order. State is immutable once set.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional


class PipelineStage(str, Enum):
    """Canonical pipeline stages in order."""
    SCRIPT = "generate_script"
    VOICE = "generate_voice"
    VIDEO = "compose_video"
    THUMBNAIL = "thumbnail"


STAGE_ORDER = (
    PipelineStage.SCRIPT,
    PipelineStage.VOICE,
    PipelineStage.VIDEO,
    PipelineStage.THUMBNAIL,
)


class PipelineStateMachine:
    """
    Hardcoded state machine for video creation. Ensures the agent cannot
    forget context or execute steps out of order.
    """

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self._current_index = -1  # -1 = not started; 0..3 = last completed index
        self._results: Dict[str, Any] = {}

    def current_stage(self) -> Optional[PipelineStage]:
        """Return the last completed stage, or None if none yet."""
        if self._current_index < 0:
            return None
        return STAGE_ORDER[self._current_index]

    def next_stage(self) -> Optional[PipelineStage]:
        """Return the next stage to run, or None if pipeline complete."""
        next_index = self._current_index + 1
        if next_index >= len(STAGE_ORDER):
            return None
        return STAGE_ORDER[next_index]

    def is_allowed(self, stage: PipelineStage) -> bool:
        """True only if stage is the next allowed step (no skip, no reorder)."""
        return self.next_stage() == stage

    def complete_stage(self, stage: PipelineStage, result: Dict[str, Any]) -> None:
        """
        Mark stage as complete and store result. Raises if stage is not the next allowed.
        """
        if not self.is_allowed(stage):
            raise ValueError(
                f"Pipeline state: expected next stage {self.next_stage()}, got {stage}. "
                "Steps must run in order: Script -> Voice -> Video -> Thumbnail."
            )
        self._current_index += 1
        self._results[stage.value] = result

    def get_result(self, stage: PipelineStage) -> Optional[Dict[str, Any]]:
        return self._results.get(stage.value)

    def is_complete(self) -> bool:
        return self._current_index >= len(STAGE_ORDER) - 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "current_stage": self.current_stage().value if self.current_stage() else None,
            "next_stage": self.next_stage().value if self.next_stage() else None,
            "completed_stages": [s.value for i, s in enumerate(STAGE_ORDER) if i <= self._current_index],
            "complete": self.is_complete(),
        }
