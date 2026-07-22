import sys
from types import ModuleType

from fastapi import FastAPI

# Mock the entire reachy_mini SDK and its submodules so we don't need
# GStreamer or native libraries to run unit tests.
reachy_module = ModuleType("reachy_mini")
utils_module = ModuleType("reachy_mini.utils")
utils_module.create_head_pose = lambda **kwargs: kwargs

utils_module.interpolation = ModuleType("reachy_mini.utils.interpolation")
utils_module.interpolation.linear_pose_interpolation = lambda *args, **kwargs: []

motion_module = ModuleType("reachy_mini.motion")
motion_module.recorded_move = ModuleType("reachy_mini.motion.recorded_move")
class FakeRecordedMoves:
    def __init__(self, *args, **kwargs):
        pass
motion_module.recorded_move.RecordedMoves = FakeRecordedMoves

class FakeReachyMini:
    def __init__(self, *args, **kwargs):
        pass

class FakeReachyMiniApp:
    def __init__(self, *args, **kwargs):
        self.settings_app = FastAPI()

reachy_module.ReachyMini = FakeReachyMini
reachy_module.ReachyMiniApp = FakeReachyMiniApp

sys.modules["reachy_mini"] = reachy_module
sys.modules["reachy_mini.utils"] = utils_module
sys.modules["reachy_mini.utils.interpolation"] = utils_module.interpolation
sys.modules["reachy_mini.motion"] = motion_module
sys.modules["reachy_mini.motion.recorded_move"] = motion_module.recorded_move
