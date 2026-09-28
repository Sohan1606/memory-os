from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from zroq.contracts import ActionStatus, GovernanceState, MemoryState
from zroq.core import CoreConfig, ZorqCore
from zroq.memory import TestMemoryOSAdapter, UnavailableMemoryOSAdapter


class CoreTests(unittest.TestCase):
    def make_core(self, root: Path, memory=None) -> ZorqCore:
        return ZorqCore(
            CoreConfig(
                owner_id="owner-1",
                device_id="device-1",
                owner_secret="correct-secret",
                approved_roots=(root,),
            ),
            memory=memory,
        )

    def test_core_boots_and_default_memory_fails_closed_for_side_effects(self):
        with TemporaryDirectory() as tmp:
            core = self.make_core(Path(tmp))
            session = core.establish_owner_session("correct-secret")
            self.assertIsNotNone(session)
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(Path(tmp) / "blocked")})
            confirmation = core.confirm(action, session)
            result = core.execute_action(action, session, confirmation)
            self.assertEqual(result.status, ActionStatus.DENIED)
            self.assertIn("memoryos_governance", result.message)
            self.assertFalse((Path(tmp) / "blocked").exists())

    def test_read_only_time_is_real_and_verified_without_memory_governance(self):
        with TemporaryDirectory() as tmp:
            core = self.make_core(Path(tmp))
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "local.time", "read_current_time", {})
            result = core.execute_action(action, session)
            self.assertEqual(result.status, ActionStatus.VERIFIED)
            self.assertEqual(result.verification.confidence_class, "E3")
            self.assertIn("iso_time", result.execution.data)

    def test_unknown_request_is_not_invented(self):
        with TemporaryDirectory() as tmp:
            core = self.make_core(Path(tmp))
            session = core.establish_owner_session("correct-secret")
            result = core.handle("download my bank statement and send it", session)
            self.assertEqual(result.status, "REPORT_UNAVAILABLE")
            self.assertEqual(result.action_results, ())

    def test_suggestions_are_not_actions(self):
        with TemporaryDirectory() as tmp:
            core = self.make_core(Path(tmp))
            session = core.establish_owner_session("correct-secret")
            result = core.handle("I need to prepare for tomorrow's meeting", session)
            self.assertEqual(result.status, "SUGGESTIONS")
            self.assertGreaterEqual(len(result.proposals), 3)
            self.assertEqual(result.action_results, ())

    def test_memory_adapter_states_are_explicit(self):
        adapter = UnavailableMemoryOSAdapter()
        context = adapter.read("owner-1", "task-1", "test")
        self.assertEqual(context.state, MemoryState.UNAVAILABLE)
        self.assertNotIn("fabricated", context.items.__repr__())

    def test_test_double_is_not_default(self):
        self.assertEqual(TestMemoryOSAdapter().contract_version, "TEST-DOUBLE-not-MEMORY//OS")


if __name__ == "__main__":
    unittest.main()
