from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest

from zroq.contracts import ActionStatus, AssuranceLevel, Confirmation, ExecutionLease, ExecutionObservation, utc_now
from zroq.core import CoreConfig, ZorqCore
from zroq.memory import TestMemoryOSAdapter


class Phase21HardeningTests(unittest.TestCase):
    def make_core(self, root: Path) -> ZorqCore:
        return ZorqCore(CoreConfig("owner-1", "device-1", "correct-secret", (root,)), memory=TestMemoryOSAdapter())

    def capture_kernel_issued_lease(self, core: ZorqCore, session):
        action = core.build_action(session, "local.time", "read_current_time", {})
        captured = {}
        original_execute = core.device_agent.execute

        def capture(action_arg, cancel_event, lease):
            captured["lease"] = lease
            return original_execute(action_arg, cancel_event, lease)

        core.device_agent.execute = capture
        result = core.execute_action(action, session)
        core.device_agent.execute = original_execute
        self.assertEqual(result.status, ActionStatus.VERIFIED)
        return action, captured["lease"]

    def test_device_agent_rejects_forged_valid_looking_lease(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            target = root / "forged-valid-looking"
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(target)})
            now = utc_now()
            forged = ExecutionLease(
                lease_id="forged-but-shaped-correctly",
                action_id=action.action_id,
                action_digest=action.digest(),
                owner_id=action.owner_id,
                principal_id=action.principal_id,
                device_id=action.device_id,
                capability_id=action.capability_id,
                capability_version=action.capability_version,
                operation=action.operation,
                issued_at=now,
                expires_at=now + timedelta(seconds=30),
                issuer_id="zorq-action-kernel",
                policy_version=core.authority.policy_version,
                security_epoch=core.security_epoch.current(),
                issuer_signature="attacker-not-a-real-proof",
            )
            observation = core.device_agent.execute(action, threading.Event(), forged)
            self.assertFalse(observation.executed)
            self.assertIn("lease", observation.error)
            self.assertFalse(target.exists())

    def test_lease_issuer_proof_is_bound_to_action_digest(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action, lease = self.capture_kernel_issued_lease(core, session)
            tampered = replace(lease, action_digest="0" * 64)
            observation = core.device_agent.execute(action, threading.Event(), tampered)
            self.assertFalse(observation.executed)
            self.assertIn("lease", observation.error)

    def test_lease_invalid_after_security_epoch_change(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action, lease = self.capture_kernel_issued_lease(core, session)
            core.emergency_stop("epoch-test")
            new_session = core.resume_after_stop("correct-secret")
            self.assertIsNotNone(new_session)
            observation = core.device_agent.execute(action, threading.Event(), lease)
            self.assertFalse(observation.executed)
            self.assertIn("lease", observation.error)

    def test_lease_for_another_device_fails(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action, lease = self.capture_kernel_issued_lease(core, session)
            wrong_device_action = replace(action, device_id="device-2")
            observation = core.device_agent.execute(wrong_device_action, threading.Event(), lease)
            self.assertFalse(observation.executed)
            self.assertIn("lease", observation.error)

    def test_stale_lease_fails(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action, lease = self.capture_kernel_issued_lease(core, session)
            expired = replace(lease, issued_at=utc_now() - timedelta(seconds=60), expires_at=utc_now() - timedelta(seconds=30))
            observation = core.device_agent.execute(action, threading.Event(), expired)
            self.assertFalse(observation.executed)
            self.assertIn("lease", observation.error)

    def test_concurrent_same_idempotency_key_dispatches_once(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            marker = root / "dispatch-count.txt"
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "once")})
            confirmation = core.confirm(action, session)
            original_execute = core.device_agent.execute
            dispatch_lock = threading.Lock()

            def counted_execute(action_arg, cancel_event, lease):
                with dispatch_lock:
                    marker.write_text(marker.read_text() + "x" if marker.exists() else "x")
                time.sleep(0.1)
                return original_execute(action_arg, cancel_event, lease)

            core.device_agent.execute = counted_execute
            results = []
            barrier = threading.Barrier(3)

            def run():
                barrier.wait()
                results.append(core.execute_action(action, session, confirmation))

            threads = [threading.Thread(target=run) for _ in range(2)]
            for thread in threads: thread.start()
            barrier.wait()
            for thread in threads: thread.join(timeout=3)
            self.assertEqual(marker.read_text(), "x")
            self.assertEqual(len(results), 2)
            self.assertTrue((root / "once").is_dir())
            self.assertTrue(all(result.status == ActionStatus.VERIFIED for result in results))

    def test_idempotency_conflict_rejects_changed_digest(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "one")})
            changed = replace(action, parameters={"path": str(root / "two")})
            first = core.execute_action(action, session, core.confirm(action, session))
            conflict = core.execute_action(changed, session, core.confirm(changed, session))
            self.assertEqual(first.status, ActionStatus.VERIFIED)
            self.assertEqual(conflict.status, ActionStatus.FAILED)
            self.assertFalse((root / "two").exists())

    def test_grant_max_calls_is_enforced(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            first_action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "first")})
            second_action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "second")})
            first = core.execute_action(first_action, session, core.confirm(first_action, session))
            second = core.execute_action(second_action, session, core.confirm(second_action, session))
            self.assertEqual(first.status, ActionStatus.VERIFIED)
            self.assertEqual(second.status, ActionStatus.DENIED)
            self.assertIn("max_calls", second.message)
            self.assertTrue((root / "first").is_dir())
            self.assertFalse((root / "second").exists())
            self.assertTrue(any(event.event_type == "grant.call_consumed" for event in core.audit.events()))
            self.assertTrue(any(event.event_type == "grant.call_rejected" for event in core.audit.events()))

    def test_concurrent_grant_call_accounting(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            actions = [
                core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "a")}),
                core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "b")}),
            ]
            results = []
            barrier = threading.Barrier(3)

            def run(action):
                barrier.wait()
                results.append(core.execute_action(action, session, core.confirm(action, session)))

            threads = [threading.Thread(target=run, args=(action,)) for action in actions]
            for thread in threads: thread.start()
            barrier.wait()
            for thread in threads: thread.join(timeout=3)
            statuses = sorted(result.status.value for result in results)
            self.assertEqual(statuses, sorted([ActionStatus.VERIFIED.value, ActionStatus.DENIED.value]))
            self.assertEqual(sum((root / name).exists() for name in ("a", "b")), 1)

    def test_generic_command_capability_is_unavailable(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            resolution = core.registry.resolve("command.allowlist", "execute_allowlisted_command")
            self.assertFalse(resolution.available)
            self.assertEqual(resolution.reason, "REPORT_UNAVAILABLE")

    def test_application_allowlist_cannot_become_shell_path(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            resolution = core.registry.resolve("application.allowlist", "open_allowlisted_application")
            self.assertFalse(resolution.available)
            self.assertEqual(resolution.reason, "REPORT_UNAVAILABLE")

    def test_resume_requires_authenticated_owner(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            core.establish_owner_session("correct-secret")
            core.emergency_stop("resume-test")
            self.assertIsNone(core.resume_after_stop("wrong-secret"))
            action_session = core.establish_owner_session("correct-secret")
            action = core.build_action(action_session, "filesystem.approved", "create_directory", {"path": str(root / "still-stopped")})
            stopped = core.execute_action(action, action_session)
            self.assertEqual(stopped.status, ActionStatus.STOPPED)
            resumed_session = core.resume_after_stop("correct-secret")
            self.assertIsNotNone(resumed_session)

    def test_emergency_stop_invalidates_old_leases(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action, lease = self.capture_kernel_issued_lease(core, session)
            core.emergency_stop("invalidate")
            resumed_session = core.resume_after_stop("correct-secret")
            self.assertIsNotNone(resumed_session)
            observation = core.device_agent.execute(action, threading.Event(), lease)
            self.assertFalse(observation.executed)
            self.assertIn("lease", observation.error)

    def test_confirmation_assurance_is_checked(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "weak-confirmation")})
            good = core.confirm(action, session)
            weak = Confirmation(
                confirmation_id="weak",
                principal_id=good.principal_id,
                session_id=good.session_id,
                action_digest=good.action_digest,
                method=good.method,
                issued_at=good.issued_at,
                expires_at=good.expires_at,
                assurance=AssuranceLevel.A0,
                accepted=True,
                policy_version=good.policy_version,
                security_epoch=good.security_epoch,
            )
            result = core.execute_action(action, session, weak)
            self.assertEqual(result.status, ActionStatus.AUTHORIZATION_REQUIRED)
            self.assertIn("assurance", result.message)
            self.assertFalse((root / "weak-confirmation").exists())

    def test_confirmation_invalid_after_security_epoch_change(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "old-confirmation")})
            confirmation = core.confirm(action, session)
            core.security_epoch.bump()
            result = core.execute_action(action, session, confirmation)
            self.assertIn(result.status, {ActionStatus.AUTHORIZATION_REQUIRED, ActionStatus.DENIED})
            self.assertTrue("epoch" in result.message or "session" in result.message)
            self.assertFalse((root / "old-confirmation").exists())

    def test_stop_before_dispatch(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            core.emergency_stop("before-dispatch")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "blocked")})
            result = core.execute_action(action, session)
            self.assertEqual(result.status, ActionStatus.STOPPED)
            self.assertFalse((root / "blocked").exists())

    def test_stop_during_execution_cancels_inflight(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "during-stop")})
            confirmation = core.confirm(action, session)
            started = threading.Event()
            original_execute = core.device_agent.execute

            def slow_execute(action_arg, cancel_event, lease):
                started.set()
                deadline = time.time() + 2
                while time.time() < deadline and not cancel_event.is_set():
                    time.sleep(0.01)
                if cancel_event.is_set():
                    return ExecutionObservation(action_arg.action_id, False, False, error="cancelled_before_commit", cancelled=True)
                return original_execute(action_arg, cancel_event, lease)

            core.device_agent.execute = slow_execute
            holder = {}
            thread = threading.Thread(target=lambda: holder.setdefault("result", core.execute_action(action, session, confirmation)))
            thread.start()
            self.assertTrue(started.wait(timeout=1))
            core.emergency_stop("during-execution")
            thread.join(timeout=3)
            self.assertIn(holder["result"].status, {ActionStatus.CANCELLED, ActionStatus.FAILED, ActionStatus.UNKNOWN})
            self.assertFalse((root / "during-stop").exists())


if __name__ == "__main__":
    unittest.main()
