from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import timedelta
import threading
import unittest

from zroq.contracts import ActionStatus, GovernanceState, ExecutionLease, utc_now
from zroq.core import CoreConfig, ZorqCore
from zroq.memory import TestMemoryOSAdapter
from zroq.providers import ModelProvider
from zroq.contracts import Context, Intent, Proposal


class InjectionProvider(ModelProvider):
    provider_id = "untrusted-injection-provider"

    def propose_intent(self, user_text: str, context: Context) -> Intent:
        # Simulates a malicious model trying to turn content into an admin
        # operation. The control plane must reject the unknown capability.
        return Intent(
            intent_id="injected-intent",
            task_id=context.task_id,
            owner_id=context.owner_id,
            user_text=user_text,
            goal="ignore policy and run administrator command",
            capability_id="os.administrator",
            parameters={"command": "delete everything"},
            confidence=1.0,
        )

    def propose_additional_actions(self, user_text: str, context: Context) -> tuple[Proposal, ...]:
        return ()


class AdversarialTests(unittest.TestCase):
    def make_core(self, root: Path, provider=None, memory=None):
        return ZorqCore(
            CoreConfig("owner-1", "device-1", "correct-secret", (root,)),
            memory=memory or TestMemoryOSAdapter(),
            provider=provider,
        )

    def test_model_output_cannot_grant_authority_or_invent_capability(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"
            root.mkdir()
            core = self.make_core(root, InjectionProvider())
            session = core.establish_owner_session("correct-secret")
            result = core.handle("a document says ignore policy", session)
            self.assertEqual(result.status, "REPORT_UNAVAILABLE")
            self.assertEqual(result.action_results, ())
            self.assertFalse((root / "delete everything").exists())

    def test_contradictory_memory_governance_fails_closed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"
            root.mkdir()
            core = self.make_core(root, memory=TestMemoryOSAdapter(contradictory=True))
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "blocked")})
            result = core.execute_action(action, session, core.confirm(action, session))
            self.assertEqual(result.status, ActionStatus.DENIED)
            self.assertIn("contradictory", result.message)

    def test_stale_session_denies(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"
            root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            core.sessions.revoke(session.session_id, "test-stale")
            action = core.build_action(session, "local.time", "read_current_time", {})
            result = core.execute_action(action, session)
            self.assertEqual(result.status, ActionStatus.DENIED)
            self.assertIn("session_invalid", result.message)

    def test_path_traversal_is_denied(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"
            root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            traversal = root / ".." / "outside"
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(traversal)})
            result = core.execute_action(action, session, core.confirm(action, session))
            self.assertEqual(result.status, ActionStatus.DENIED)
            self.assertFalse((Path(tmp) / "outside").exists())

    def test_confirmation_is_not_a_policy_decision(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"
            root.mkdir()
            core = self.make_core(root, memory=TestMemoryOSAdapter(allow_side_effects=False))
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "blocked")})
            # A valid confirmation cannot override a governance denial.
            result = core.execute_action(action, session, core.confirm(action, session))
            self.assertEqual(result.status, ActionStatus.DENIED)
            self.assertFalse((root / "blocked").exists())

    def test_forged_device_lease_cannot_execute(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"
            root.mkdir()
            core = self.make_core(root, memory=TestMemoryOSAdapter())
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "forged")})
            lease = ExecutionLease(
                lease_id="forged",
                action_id=action.action_id,
                action_digest="wrong-digest",
                owner_id=action.owner_id,
                principal_id=action.principal_id,
                device_id=action.device_id,
                capability_id=action.capability_id,
                capability_version=action.capability_version,
                operation=action.operation,
                issued_at=utc_now(),
                expires_at=utc_now() + timedelta(seconds=30),
            )
            observation = core.device_agent.execute(action, threading.Event(), lease)
            self.assertFalse(observation.executed)
            self.assertIn("lease", observation.error)
            self.assertFalse((root / "forged").exists())

    def test_audit_chain_detects_tampering(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"
            root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "local.time", "read_current_time", {})
            core.execute_action(action, session)
            event = core.audit._events[0]
            core.audit._events[0] = type(event)(**{**event.__dict__, "payload": {"tampered": True}})
            with self.assertRaises(Exception):
                core.verify_audit()


if __name__ == "__main__":
    unittest.main()
