"""Small, safe motions used as voice-state indicators."""

from __future__ import annotations

import logging

import numpy as np
from reachy_mini.utils import create_head_pose

_LOGGER = logging.getLogger(__name__)


class VoiceMotion:
    """Express voice state without a competing real-time motor loop."""

    def __init__(self, robot: object, *, enabled: bool = True) -> None:
        self.robot = robot
        self.enabled = enabled

    def _pose(
        self,
        *,
        pitch: float = 0.0,
        roll: float = 0.0,
        yaw: float = 0.0,
        right_antenna: float = 0.0,
        left_antenna: float = 0.0,
        duration: float = 0.35,
    ) -> None:
        if not self.enabled:
            return
        try:
            head = create_head_pose(pitch=pitch, roll=roll, yaw=yaw, degrees=True)
            antennas = np.deg2rad(np.asarray([right_antenna, left_antenna], dtype=np.float64))
            self.robot.goto_target(head=head, antennas=antennas, duration=duration)
        except Exception as exc:
            _LOGGER.warning("Could not apply Reachy voice pose: %s", exc)

    def listening(self) -> None:
        self._pose(pitch=-4.0, right_antenna=16.0, left_antenna=-16.0)

    def thinking(self) -> None:
        self._pose(pitch=-2.0, roll=6.0, right_antenna=8.0, left_antenna=-22.0, duration=0.45)

    def speaking(self) -> None:
        self._pose(pitch=3.0, roll=-2.0, right_antenna=20.0, left_antenna=-20.0, duration=0.3)

    def idle(self) -> None:
        self._pose(duration=0.45)

    def error(self) -> None:
        self._pose(pitch=5.0, roll=-7.0, right_antenna=-8.0, left_antenna=8.0, duration=0.35)
