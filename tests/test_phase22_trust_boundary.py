from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import platform
from tempfile import TemporaryDirectory
import threading
import time
import unittest

from zroq.capabilities import CapabilityManifest
from zroq.confirmation import issue_confirmation
from zroq.contracts import (
    ActionStatus,
    AssuranceLevel,
    ConfirmationMode,
    ExecutionLease,
    PrincipalType,
    RiskLevel,
    Session,
    utc_now,
)
from zroq.core import CoreConfig, ZorqCore
from zroq.memory import TestMemoryOSAdapter
from zroq.security import FileSystemPosture, ResourceLimits


class Phase22TrustBoundaryTests(unittest.TestCase):
    def make_core(self, root: Path, limits: ResourceLimits | None = None) -> ZorqCore:
        return ZorqCore(
            CoreConfig("owner-1", "device-1", "correct-secret", (root,), resource_limits=limits or ResourceLimits()),
            memory=TestMemoryOSAdapter(),
        )

    def test_core_does_not_expose_direct_lease_issuer(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            self.assertFalse(hasattr(core, "lease_issuer"))
            self.assertFalse(hasattr(core.device_agent, "lease_issuer"))
            self.assertFalse(hasattr(core.registry, "lease_issuer"))
            self.assertFalse(hasattr(core.kernel, "lease_issuer"))

    def test_kernel_rejects_revoked_session_object(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            core.sessions.revoke(session.session_id, "phase22-test")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "revoked")})
            confirmation = issue_confirmation(action, session, policy_version=core.authority.policy_version, security_epoch=core.security_epoch.current())
            result = core.kernel.execute(action, session, confirmation)
            self.assertEqual(result.status, ActionStatus.DENIED)
            self.assertIn("session_authority", result.message)
            self.assertFalse((root / "revoked").exists())

    def test_kernel_requires_authoritative_session_validation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            fake = replace(session, session_id="not-in-authoritative-store")
            action = core.build_action(fake, "filesystem.approved", "create_directory", {"path": str(root / "fake")})
            confirmation = issue_confirmation(action, fake, policy_version=core.authority.policy_version, security_epoch=core.security_epoch.current())
            result = core.kernel.execute(action, fake, confirmation)
            self.assertEqual(result.status, ActionStatus.DENIED)
            self.assertIn("session_authority", result.message)
            self.assertFalse((root / "fake").exists())

    def test_old_session_cannot_execute_after_stop_and_resume(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session_a = core.establish_owner_session("correct-secret")
            core.emergency_stop("phase22")
            session_b = core.resume_after_stop("correct-secret")
            self.assertIsNotNone(session_b)
            action = core.build_action(session_a, "filesystem.approved", "create_directory", {"path": str(root / "old-session")})
            confirmation = issue_confirmation(action, session_a, policy_version=core.authority.policy_version, security_epoch=core.security_epoch.current())
            result = core.kernel.execute(action, session_a, confirmation)
            self.assertEqual(result.status, ActionStatus.DENIED)
            self.assertIn("session_authority", result.message)
            self.assertFalse((root / "old-session").exists())

    def test_new_session_required_after_recovery(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session_a = core.establish_owner_session("correct-secret")
            core.emergency_stop("phase22")
            session_b = core.resume_after_stop("correct-secret")
            self.assertIsNotNone(session_b)
            old_action = core.build_action(session_a, "filesystem.approved", "create_directory", {"path": str(root / "old")})
            old_confirmation = issue_confirmation(old_action, session_a, policy_version=core.authority.policy_version, security_epoch=core.security_epoch.current())
            old_result = core.kernel.execute(old_action, session_a, old_confirmation)
            self.assertEqual(old_result.status, ActionStatus.DENIED)
            fresh_action = core.build_action(session_b, "filesystem.approved", "create_directory", {"path": str(root / "fresh")})
            fresh_result = core.execute_action(fresh_action, session_b, core.confirm(fresh_action, session_b))
            self.assertEqual(fresh_result.status, ActionStatus.VERIFIED)
            self.assertTrue((root / "fresh").is_dir())
            self.assertFalse((root / "old").exists())

    def test_unexpected_device_exception_finalizes_idempotency(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "exception")})
            confirmation = core.confirm(action, session)

            def boom(*args, **kwargs):
                raise RuntimeError("unexpected-device-crash")

            core.device_agent.execute = boom
            result = core.execute_action(action, session, confirmation)
            self.assertIn(result.status, {ActionStatus.FAILED, ActionStatus.UNKNOWN})
            self.assertNotEqual(result.status, ActionStatus.VERIFIED)
            record = core.kernel._idempotency[action.idempotency_key]
            self.assertIs(record.result, result)
            self.assertTrue(record.event.is_set())
            self.assertNotIn(action.action_id, core.kernel._cancel_events)

    def test_unexpected_device_exception_does_not_leave_active_lease(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "exception")})
            core.device_agent.execute = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
            result = core.execute_action(action, session, core.confirm(action, session))
            self.assertIn(result.status, {ActionStatus.FAILED, ActionStatus.UNKNOWN})
            self.assertEqual(core.kernel.active_lease_count(), 0)

    def test_unexpected_device_exception_wakes_idempotent_waiters(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "waiters")})
            confirmation = core.confirm(action, session)
            calls = 0
            lock = threading.Lock()

            def boom(*args, **kwargs):
                nonlocal calls
                with lock:
                    calls += 1
                time.sleep(0.1)
                raise RuntimeError("boom")

            core.device_agent.execute = boom
            results = []
            barrier = threading.Barrier(3)

            def run():
                barrier.wait()
                results.append(core.execute_action(action, session, confirmation))

            threads = [threading.Thread(target=run) for _ in range(2)]
            for thread in threads: thread.start()
            barrier.wait()
            for thread in threads: thread.join(timeout=3)
            self.assertEqual(calls, 1)
            self.assertEqual(len(results), 2)
            self.assertTrue(all(r.status != ActionStatus.VERIFIED for r in results))
            self.assertTrue(all(r.status in {ActionStatus.FAILED, ActionStatus.UNKNOWN} for r in results))

    def test_unexpected_device_exception_records_audit(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "audit")})
            core.device_agent.execute = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
            core.execute_action(action, session, core.confirm(action, session))
            self.assertTrue(any(event.event_type == "action.execution_exception" and event.action_id == action.action_id for event in core.audit.events()))
            self.assertTrue(core.verify_audit())

    def test_unexpected_device_exception_never_returns_verified(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "no-verify")})
            core.device_agent.execute = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
            result = core.execute_action(action, session, core.confirm(action, session))
            self.assertNotEqual(result.status, ActionStatus.VERIFIED)
            self.assertFalse((root / "no-verify").exists())

    def test_stop_during_unexpected_execution_exception_finalizes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "stop-exception")})
            confirmation = core.confirm(action, session)
            entered = threading.Event()

            def boom_after_stop(*args, **kwargs):
                entered.set()
                deadline = time.time() + 2
                while time.time() < deadline and not core.device_agent.is_stopped():
                    time.sleep(0.01)
                raise RuntimeError("boom-after-stop")

            core.device_agent.execute = boom_after_stop
            holder = {}
            thread = threading.Thread(target=lambda: holder.setdefault("result", core.execute_action(action, session, confirmation)))
            thread.start()
            self.assertTrue(entered.wait(timeout=1))
            core.emergency_stop("during-exception")
            thread.join(timeout=3)
            self.assertIn(holder["result"].status, {ActionStatus.FAILED, ActionStatus.UNKNOWN})
            self.assertEqual(core.kernel.active_lease_count(), 0)
            self.assertTrue(core.verify_audit())

    def test_read_output_limit_enforced(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            limits = ResourceLimits(max_file_bytes=4096, max_output_bytes=16)
            core = self.make_core(root, limits)
            session = core.establish_owner_session("correct-secret")
            path = root / "large.txt"
            path.write_text("x" * 64, encoding="utf-8")
            action = core.build_action(session, "filesystem.approved", "read_text_file", {"path": str(path)})
            result = core.execute_action(action, session, core.confirm(action, session))
            self.assertEqual(result.status, ActionStatus.FAILED)
            self.assertIn("read_output_limit_exceeded", result.message)

    def test_read_output_limit_never_exceeds_manifest(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            limits = ResourceLimits(max_file_bytes=4096, max_output_bytes=16)
            core = self.make_core(root, limits)
            session = core.establish_owner_session("correct-secret")
            path = root / "large.txt"
            path.write_text("x" * 64, encoding="utf-8")
            action = core.build_action(session, "filesystem.approved", "read_text_file", {"path": str(path)})
            result = core.execute_action(action, session, core.confirm(action, session))
            content = result.execution.data.get("content") if result.execution else None
            self.assertTrue(content is None or len(content.encode("utf-8")) <= limits.max_output_bytes)

    def test_large_file_read_has_truthful_status(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            limits = ResourceLimits(max_file_bytes=4096, max_output_bytes=16)
            core = self.make_core(root, limits)
            session = core.establish_owner_session("correct-secret")
            path = root / "large.txt"
            path.write_text("x" * 64, encoding="utf-8")
            action = core.build_action(session, "filesystem.approved", "read_text_file", {"path": str(path)})
            result = core.execute_action(action, session, core.confirm(action, session))
            self.assertNotEqual(result.status, ActionStatus.VERIFIED)
            self.assertFalse(result.execution.executed)

    def test_filesystem_posture_is_explicit(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            self.assertIn(core.device_policy.posture, {FileSystemPosture.SUPPORTED, FileSystemPosture.DEGRADED})
            self.assertTrue(core.device_policy.posture_reason)

    def test_root_replacement_detected_where_identity_supported(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            old = Path(tmp) / "old-approved"
            try:
                root.rename(old)
                root.mkdir()
            except OSError as exc:
                self.skipTest(f"root replacement setup unavailable: {exc}")
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "blocked")})
            result = core.execute_action(action, session, core.confirm(action, session))
            self.assertEqual(result.status, ActionStatus.FAILED)
            self.assertIn("approved_root", result.message)
            self.assertFalse((root / "blocked").exists())

    def test_parent_directory_substitution_denied(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            outside = Path(tmp) / "outside"; outside.mkdir()
            link = root / "parent-link"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlink unavailable: {exc}")
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "create_text_file", {"path": str(link / "escape.txt"), "content": "no"})
            result = core.execute_action(action, session, core.confirm(action, session))
            self.assertEqual(result.status, ActionStatus.DENIED)
            self.assertFalse((outside / "escape.txt").exists())

    def test_direct_device_agent_call_with_forged_lease_fails(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "local.time", "read_current_time", {})
            now = utc_now()
            forged = ExecutionLease(
                lease_id="forged",
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
                issuer_signature="not-valid",
            )
            observation = core.device_agent.execute(action, threading.Event(), forged)
            self.assertFalse(observation.executed)

    def test_direct_device_agent_call_after_stop_fails(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "local.time", "read_current_time", {})
            core.emergency_stop("direct-agent-stop")
            forged = ExecutionLease("x", action.action_id, action.digest(), action.owner_id, action.principal_id, action.device_id, action.capability_id, action.capability_version, action.operation, utc_now(), utc_now() + timedelta(seconds=5), issuer_id="zorq-action-kernel", policy_version=core.authority.policy_version, security_epoch=core.security_epoch.current(), issuer_signature="invalid")
            observation = core.device_agent.execute(action, threading.Event(), forged)
            self.assertFalse(observation.executed)
            self.assertTrue(observation.cancelled)

    def test_direct_kernel_call_during_stop_returns_stopped(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "local.time", "read_current_time", {})
            core.emergency_stop("direct-kernel-stop")
            result = core.kernel.execute(action, session)
            self.assertEqual(result.status, ActionStatus.STOPPED)

    def test_runtime_capability_installation_is_unavailable(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            dangerous = CapabilityManifest(
                capability_id="dangerous.test",
                version="1.0.0",
                operations=("do_danger",),
                permissions=("dangerous.do",),
                risk=RiskLevel.R1,
                input_schema={},
                output_schema={},
                cancellation="none",
                verification="none",
                audit_events=(),
                failure_behavior="forbidden",
                timeout_seconds=1,
                resource_limits={},
                confirmation_mode=ConfirmationMode.EXPLICIT,
                description="should not install at runtime",
            )
            with self.assertRaises(PermissionError):
                core.registry.register(dangerous)
            self.assertFalse(core.registry.resolve("dangerous.test").available)

    def test_public_grants_mapping_is_not_mutable(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            with self.assertRaises((TypeError, AttributeError)):
                core.grants.pop(("owner-1", "filesystem.approved", "create_directory"))

    def test_direct_confirmation_construction_cannot_overcome_session_revocation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "confirm-forged")})
            core.sessions.revoke(session.session_id, "test")
            confirmation = issue_confirmation(action, session, policy_version=core.authority.policy_version, security_epoch=core.security_epoch.current())
            result = core.kernel.execute(action, session, confirmation)
            self.assertEqual(result.status, ActionStatus.DENIED)
            self.assertFalse((root / "confirm-forged").exists())

    def test_direct_replay_of_completed_action_does_not_duplicate_effect(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "once")})
            confirmation = core.confirm(action, session)
            first = core.execute_action(action, session, confirmation)
            second = core.execute_action(action, session, confirmation)
            self.assertEqual(first.status, ActionStatus.VERIFIED)
            self.assertEqual(second.status, ActionStatus.VERIFIED)
            self.assertEqual(len([p for p in root.iterdir() if p.name == "once"]), 1)


@unittest.skipUnless(platform.system() == "Windows", "Windows-specific validation not executed on this platform")
class WindowsValidationTests(unittest.TestCase):
    def make_core(self, root: Path) -> ZorqCore:
        return ZorqCore(CoreConfig("owner-1", "device-1", "correct-secret", (root,)), memory=TestMemoryOSAdapter())

    def test_windows_approved_root_creation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved root"
            root.mkdir()
            core = self.make_core(root)
            self.assertIn(core.device_policy.posture, {FileSystemPosture.SUPPORTED, FileSystemPosture.DEGRADED})

    def test_windows_approved_directory_creation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "child")})
            result = core.execute_action(action, session, core.confirm(action, session))
            if core.device_policy.posture == FileSystemPosture.SUPPORTED:
                self.assertEqual(result.status, ActionStatus.VERIFIED)
            else:
                self.assertNotEqual(result.status, ActionStatus.VERIFIED)

    def test_windows_file_creation_and_read(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            path = root / "note.txt"
            create = core.build_action(session, "filesystem.approved", "create_text_file", {"path": str(path), "content": "hello"})
            created = core.execute_action(create, session, core.confirm(create, session))
            if core.device_policy.posture == FileSystemPosture.SUPPORTED:
                self.assertEqual(created.status, ActionStatus.VERIFIED)
                read = core.build_action(session, "filesystem.approved", "read_text_file", {"path": str(path)})
                result = core.execute_action(read, session, core.confirm(read, session))
                self.assertEqual(result.status, ActionStatus.VERIFIED)
            else:
                self.assertNotEqual(created.status, ActionStatus.VERIFIED)

    def test_windows_traversal_denial(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / ".." / "escape")})
            result = core.execute_action(action, session, core.confirm(action, session))
            self.assertIn(result.status, {ActionStatus.DENIED, ActionStatus.FAILED})

    def test_windows_unicode_spaces_long_paths_stop_and_lease_session_invalidation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved unicode path"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            unicode_path = root / ("unicodé " + "x" * 40)
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(unicode_path)})
            result = core.execute_action(action, session, core.confirm(action, session))
            if core.device_policy.posture == FileSystemPosture.SUPPORTED:
                self.assertEqual(result.status, ActionStatus.VERIFIED)
            core.emergency_stop("windows-validation")
            stopped = core.execute_action(core.build_action(session, "local.time", "read_current_time", {}), session)
            self.assertEqual(stopped.status, ActionStatus.STOPPED)
            resumed = core.resume_after_stop("correct-secret")
            self.assertIsNotNone(resumed)
            old_result = core.execute_action(core.build_action(session, "local.time", "read_current_time", {}), session)
            self.assertEqual(old_result.status, ActionStatus.DENIED)

    def test_windows_symlink_junction_reparse_escape_behavior_where_available(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; outside = Path(tmp) / "outside"
            root.mkdir(); outside.mkdir()
            link = root / "link"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"Windows symlink/reparse setup unavailable: {exc}")
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(link / "escape")})
            result = core.execute_action(action, session, core.confirm(action, session))
            self.assertIn(result.status, {ActionStatus.DENIED, ActionStatus.FAILED})
            self.assertFalse((outside / "escape").exists())

    def test_windows_parent_substitution_and_root_replacement_detection(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            moved = Path(tmp) / "approved-old"
            try:
                root.rename(moved)
                root.mkdir()
            except OSError as exc:
                self.skipTest(f"Windows root replacement setup unavailable: {exc}")
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "blocked")})
            result = core.execute_action(action, session, core.confirm(action, session))
            self.assertNotEqual(result.status, ActionStatus.VERIFIED)
            self.assertFalse((root / "blocked").exists())

    def test_windows_cancellation_emergency_stop_and_lease_invalidation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "local.time", "read_current_time", {})
            captured = {}
            original = core.device_agent.execute
            def capture(action_arg, cancel_event, lease):
                captured["lease"] = lease
                return original(action_arg, cancel_event, lease)
            core.device_agent.execute = capture
            result = core.execute_action(action, session)
            core.device_agent.execute = original
            self.assertEqual(result.status, ActionStatus.VERIFIED)
            core.emergency_stop("windows-lease")
            resumed = core.resume_after_stop("correct-secret")
            self.assertIsNotNone(resumed)
            old = core.device_agent.execute(action, threading.Event(), captured["lease"])
            self.assertFalse(old.executed)


if __name__ == "__main__":
    unittest.main()
