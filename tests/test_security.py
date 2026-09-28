from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from zroq.contracts import ActionStatus
from zroq.core import CoreConfig, ZorqCore
from zroq.memory import TestMemoryOSAdapter


class SecurityTests(unittest.TestCase):
    def make_core(self, approved: Path, memory=None) -> ZorqCore:
        return ZorqCore(
            CoreConfig(
                owner_id="owner-1",
                device_id="device-1",
                owner_secret="correct-secret",
                approved_roots=(approved,),
            ),
            memory=memory or TestMemoryOSAdapter(),
        )

    def test_real_filesystem_action_and_postcondition_verification(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"
            root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            target = root / "real-created-directory"
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(target)})
            confirmation = core.confirm(action, session)
            result = core.execute_action(action, session, confirmation)
            self.assertEqual(result.status, ActionStatus.VERIFIED)
            self.assertTrue(target.exists())
            self.assertTrue(target.is_dir())
            self.assertEqual(result.verification.evidence["exists"], True)
            self.assertTrue(core.verify_audit())

    def test_same_capability_cannot_escape_approved_directory(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"
            outside = Path(tmp) / "outside"
            root.mkdir()
            outside.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            target = outside / "escape"
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(target)})
            confirmation = core.confirm(action, session)
            result = core.execute_action(action, session, confirmation)
            self.assertEqual(result.status, ActionStatus.DENIED)
            self.assertIn("path_outside_grant", result.message)
            self.assertFalse(target.exists())

    def test_symlink_escape_is_denied_before_real_write(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"
            outside = Path(tmp) / "outside"
            root.mkdir()
            outside.mkdir()
            link = root / "link"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable on this platform")
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            target = link / "escape"
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(target)})
            result = core.execute_action(action, session, core.confirm(action, session))
            self.assertEqual(result.status, ActionStatus.DENIED)
            self.assertFalse(outside.joinpath("escape").exists())

    def test_confirmation_digest_mismatch_denies(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"
            root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            original = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "one")})
            confirmation = core.confirm(original, session)
            changed = replace(original, parameters={"path": str(root / "two")})
            result = core.execute_action(changed, session, confirmation)
            self.assertEqual(result.status, ActionStatus.AUTHORIZATION_REQUIRED)
            self.assertFalse((root / "one").exists())
            self.assertFalse((root / "two").exists())

    def test_confirmation_retry_does_not_cache_pre_dispatch_denial(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"
            root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "retry-after-confirmation")})
            pending = core.execute_action(action, session)
            self.assertEqual(pending.status, ActionStatus.AUTHORIZATION_REQUIRED)
            verified = core.execute_action(action, session, core.confirm(action, session))
            self.assertEqual(verified.status, ActionStatus.VERIFIED)
            self.assertTrue((root / "retry-after-confirmation").is_dir())

    def test_idempotency_prevents_mutating_retry(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"
            root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "one")})
            confirmation = core.confirm(action, session)
            first = core.execute_action(action, session, confirmation)
            second = core.execute_action(action, session, confirmation)
            self.assertEqual(first.status, ActionStatus.VERIFIED)
            self.assertEqual(second.status, ActionStatus.VERIFIED)
            changed = replace(action, parameters={"path": str(root / "two")})
            changed_result = core.execute_action(changed, session, core.confirm(changed, session))
            self.assertEqual(changed_result.status, ActionStatus.FAILED)
            self.assertFalse((root / "two").exists())

    def test_emergency_stop_blocks_new_dispatch(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"
            root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            core.emergency_stop("test")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "blocked")})
            result = core.execute_action(action, session)
            self.assertEqual(result.status, ActionStatus.STOPPED)
            self.assertFalse((root / "blocked").exists())

    def test_capability_discovery_is_not_permission(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"
            root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            core.grant_authority._remove_for_test(session.principal_id, "filesystem.approved", "create_directory", "1.0.0")
            result = core.handle(f"create directory {root / 'not-permitted'}", session)
            self.assertEqual(result.status, "REPORT_DENIED")
            self.assertFalse((root / "not-permitted").exists())

    def test_fake_success_cannot_pass_verification(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"
            root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "does-not-exist")})
            # Directly exercise verifier with an observation that claims execution
            # but did not create the postcondition.
            from zroq.contracts import ExecutionObservation
            verification = core.verifier.verify(action, ExecutionObservation(action.action_id, True, True, {"claimed": True}))
            self.assertNotEqual(verification.status.value, "verified")


if __name__ == "__main__":
    unittest.main()
