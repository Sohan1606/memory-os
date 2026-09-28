from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from zroq.contracts import ActionStatus
from zroq.core import CoreConfig, ZorqCore
from zroq.memory import TestMemoryOSAdapter


class IntegrationTests(unittest.TestCase):
    def test_orchestrator_requires_confirmation_for_r1_and_then_executes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"
            root.mkdir()
            core = ZorqCore(
                CoreConfig("owner-1", "device-1", "correct-secret", (root,)),
                memory=TestMemoryOSAdapter(),
            )
            session = core.establish_owner_session("correct-secret")
            request = f"create directory {root / 'through-orchestrator'}"
            pending = core.handle(request, session)
            self.assertEqual(pending.status, ActionStatus.AUTHORIZATION_REQUIRED.value)
            self.assertEqual(len(pending.action_results), 1)
            self.assertFalse((root / "through-orchestrator").exists())
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "through-orchestrator")})
            result = core.execute_action(action, session, core.confirm(action, session))
            self.assertEqual(result.status, ActionStatus.VERIFIED)
            self.assertTrue((root / "through-orchestrator").is_dir())

    def test_text_file_real_write_and_readback(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"
            root.mkdir()
            core = ZorqCore(CoreConfig("owner-1", "device-1", "correct-secret", (root,)), memory=TestMemoryOSAdapter())
            session = core.establish_owner_session("correct-secret")
            path = root / "note.txt"
            create = core.build_action(session, "filesystem.approved", "create_text_file", {"path": str(path), "content": "ZORQ real state\n"})
            result = core.execute_action(create, session, core.confirm(create, session))
            self.assertEqual(result.status, ActionStatus.VERIFIED)
            self.assertEqual(path.read_text(), "ZORQ real state\n")
            read = core.build_action(session, "filesystem.approved", "read_text_file", {"path": str(path)})
            read_result = core.execute_action(read, session, core.confirm(read, session))
            self.assertEqual(read_result.status, ActionStatus.VERIFIED)
            self.assertEqual(read_result.execution.data["content"], "ZORQ real state\n")

    def test_authorization_and_audit_are_deterministic_without_network(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"
            root.mkdir()
            audit_path = Path(tmp) / "audit.jsonl"
            core = ZorqCore(CoreConfig("owner-1", "device-1", "correct-secret", (root,), audit_path=audit_path), memory=TestMemoryOSAdapter())
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "local.time", "read_current_time", {})
            result = core.execute_action(action, session)
            self.assertEqual(result.status, ActionStatus.VERIFIED)
            self.assertTrue(audit_path.exists())
            self.assertTrue(core.verify_audit())
            self.assertGreaterEqual(len(core.audit.events()), 5)

    def test_generic_command_capability_is_unavailable(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"
            root.mkdir()
            core = ZorqCore(CoreConfig("owner-1", "device-1", "correct-secret", (root,)), memory=TestMemoryOSAdapter())
            resolution = core.registry.resolve("command.allowlist", "execute_allowlisted_command")
            self.assertFalse(resolution.available)
            self.assertEqual(resolution.reason, "REPORT_UNAVAILABLE")
            session = core.establish_owner_session("correct-secret")
            with self.assertRaises(ValueError):
                core.build_action(session, "command.allowlist", "execute_allowlisted_command", {"command_id": "anything"})

    def test_application_allowlist_cannot_become_shell_path(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"
            root.mkdir()
            core = ZorqCore(CoreConfig("owner-1", "device-1", "correct-secret", (root,)), memory=TestMemoryOSAdapter())
            resolution = core.registry.resolve("application.allowlist", "open_allowlisted_application")
            self.assertFalse(resolution.available)
            self.assertEqual(resolution.reason, "REPORT_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
