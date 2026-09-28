from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import stat
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from zroq.contracts import ActionRequest, ActionStatus, RiskLevel, utc_now
from zroq.core import CoreConfig, ZorqCore
from zroq.device_agent import LocalDeviceAgent
from zroq.leases import LeaseIssuer, LeaseRegistry, SecurityEpoch
from zroq.memory import TestMemoryOSAdapter
from zroq.security import FileSystemPosture, ResourceLimits, SecurityViolation
import zroq.security as security_module


class ExplodingMemory(TestMemoryOSAdapter):
    def __init__(self, gate: threading.Event | None = None):
        super().__init__()
        self.gate = gate

    def governance(self, action):
        if self.gate is not None:
            self.gate.wait(timeout=5)
        raise RuntimeError("phase25-governance-boom")


class FakeEntry:
    def __init__(self, name: str):
        self.name = name

    def lstat(self):
        return SimpleNamespace(st_size=1, st_mode=stat.S_IFREG)

    def is_dir(self):
        return False

    def is_file(self):
        return True

    def is_symlink(self):
        return False


class CountingDirectory:
    def __init__(self, count: int, fail_after: int | None = None):
        self.count = count
        self.fail_after = fail_after
        self.yielded = 0

    def exists(self):
        return True

    def is_dir(self):
        return True

    def iterdir(self):
        for idx in range(self.count):
            self.yielded += 1
            if self.fail_after is not None and self.yielded > self.fail_after:
                raise AssertionError("directory enumeration exceeded bounded limit")
            yield FakeEntry(f"entry-{idx:04d}")


class Phase25ExecutionBarrierTests(unittest.TestCase):
    def make_core(self, root: Path, memory=None, limits: ResourceLimits | None = None) -> ZorqCore:
        return ZorqCore(
            CoreConfig("owner-1", "device-1", "correct-secret", (root,), resource_limits=limits or ResourceLimits()),
            memory=memory or TestMemoryOSAdapter(),
        )

    def create_action(self, core: ZorqCore, session, target: Path):
        return core.build_action(session, "filesystem.approved", "create_directory", {"path": str(target)})

    def run_action_thread(self, core: ZorqCore, action, session, confirmation=None):
        result_box = {}

        def runner():
            try:
                result_box["result"] = core.execute_action(action, session, confirmation or core.confirm(action, session))
            except Exception as exc:  # test records pre-hardening escape
                result_box["exception"] = exc

        thread = threading.Thread(target=runner)
        thread.start()
        return thread, result_box

    def test_stop_between_lease_verification_and_commit_blocks_effect(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            target = root / "blocked-by-stop"
            action = self.create_action(core, session, target)
            before_commit = threading.Event()
            proceed = threading.Event()
            original_open_parent_fd = core.device_policy.open_parent_fd

            def delayed_open_parent_fd(parent):
                before_commit.set()
                self.assertTrue(proceed.wait(timeout=5))
                return original_open_parent_fd(parent)

            object.__setattr__(core.device_policy, "open_parent_fd", delayed_open_parent_fd)
            thread, result_box = self.run_action_thread(core, action, session)
            self.assertTrue(before_commit.wait(timeout=5))
            core.emergency_stop("phase25-stop-before-commit")
            proceed.set()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertFalse(target.exists())
            self.assertNotEqual(result_box.get("result").status, ActionStatus.VERIFIED)

    def test_old_epoch_cannot_cross_commit_barrier(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            target = root / "old-epoch"
            action = self.create_action(core, session, target)
            before_commit = threading.Event()
            proceed = threading.Event()
            original_open_parent_fd = core.device_policy.open_parent_fd

            def delayed_open_parent_fd(parent):
                before_commit.set()
                self.assertTrue(proceed.wait(timeout=5))
                return original_open_parent_fd(parent)

            object.__setattr__(core.device_policy, "open_parent_fd", delayed_open_parent_fd)
            thread, result_box = self.run_action_thread(core, action, session)
            self.assertTrue(before_commit.wait(timeout=5))
            core.security_epoch.bump()
            proceed.set()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertFalse(target.exists())
            self.assertNotEqual(result_box.get("result").status, ActionStatus.VERIFIED)

    def test_stop_during_commit_reports_truthful_outcome(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            target = root / "stop-after-commit"
            action = self.create_action(core, session, target)
            import zroq.device_agent as device_agent_module
            original_mkdir = device_agent_module.os.mkdir

            def mkdir_then_stop(name, mode=0o777, *, dir_fd=None):
                original_mkdir(name, mode=mode, dir_fd=dir_fd)
                core.emergency_stop("phase25-stop-after-commit")

            with mock.patch.object(device_agent_module.os, "mkdir", mkdir_then_stop):
                result = core.execute_action(action, session, core.confirm(action, session))
            self.assertTrue(target.exists())
            self.assertIn(result.status, {ActionStatus.VERIFIED, ActionStatus.UNKNOWN})

    def test_post_resume_requires_fresh_authorization(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            old_target = root / "old-authorized"
            old_action = self.create_action(core, session, old_target)
            before_commit = threading.Event()
            proceed = threading.Event()
            original_open_parent_fd = core.device_policy.open_parent_fd

            def delayed_open_parent_fd(parent):
                before_commit.set()
                self.assertTrue(proceed.wait(timeout=5))
                return original_open_parent_fd(parent)

            object.__setattr__(core.device_policy, "open_parent_fd", delayed_open_parent_fd)
            thread, result_box = self.run_action_thread(core, old_action, session)
            self.assertTrue(before_commit.wait(timeout=5))
            core.emergency_stop("phase25-stop-before-resume")
            fresh_session = core.resume_after_stop("correct-secret")
            self.assertIsNotNone(fresh_session)
            proceed.set()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertFalse(old_target.exists())
            self.assertNotEqual(result_box.get("result").status, ActionStatus.VERIFIED)
            fresh_target = root / "fresh-authorized.txt"
            fresh_action = core.build_action(fresh_session, "filesystem.approved", "create_text_file", {"path": str(fresh_target), "content": "fresh"})
            fresh_result = core.execute_action(fresh_action, fresh_session, core.confirm(fresh_action, fresh_session))
            self.assertEqual(fresh_result.status, ActionStatus.VERIFIED)
            self.assertTrue(fresh_target.exists())


class Phase25FailureContainmentTests(unittest.TestCase):
    def make_core(self, root: Path, memory=None) -> ZorqCore:
        return ZorqCore(CoreConfig("owner-1", "device-1", "correct-secret", (root,)), memory=memory or TestMemoryOSAdapter())

    def action(self, core: ZorqCore, session, name="target", timeout=0.2):
        action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(Path(core.config.approved_roots[0]) / name)})
        return replace(action, timeout_seconds=timeout)

    def test_governance_exception_finalizes_idempotency(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root, memory=ExplodingMemory())
            session = core.establish_owner_session("correct-secret")
            action = self.action(core, session, "governance")
            result = core.kernel.execute(action, session, core.confirm(action, session))
            self.assertEqual(result.status, ActionStatus.FAILED)
            self.assertIn("unexpected_internal_exception", result.message)
            self.assertFalse((root / "governance").exists())

    def test_authority_exception_finalizes_idempotency(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = self.action(core, session, "authority")

            def boom(*args, **kwargs):
                raise RuntimeError("phase25-authority-boom")

            core.authority.evaluate = boom
            result = core.kernel.execute(action, session, core.confirm(action, session))
            self.assertEqual(result.status, ActionStatus.FAILED)
            self.assertIn("unexpected_internal_exception", result.message)
            self.assertFalse((root / "authority").exists())

    def test_lease_issue_exception_finalizes_idempotency(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = self.action(core, session, "lease-issue")

            def boom(*args, **kwargs):
                raise RuntimeError("phase25-lease-issue-boom")

            core.kernel._ActionKernel__lease_issuer.issue = boom
            result = core.kernel.execute(action, session, core.confirm(action, session))
            self.assertEqual(result.status, ActionStatus.FAILED)
            self.assertIn("unexpected_internal_exception", result.message)
            self.assertFalse((root / "lease-issue").exists())

    def test_waiters_are_woken_on_internal_exception(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            gate = threading.Event()
            core = self.make_core(root, memory=ExplodingMemory(gate))
            session = core.establish_owner_session("correct-secret")
            action = self.action(core, session, "waiters", timeout=0.2)
            confirmation = core.confirm(action, session)
            results = []
            errors = []

            def call():
                try:
                    results.append(core.kernel.execute(action, session, confirmation))
                except Exception as exc:
                    errors.append(exc)

            t1 = threading.Thread(target=call)
            t2 = threading.Thread(target=call)
            t1.start()
            time.sleep(0.05)
            t2.start()
            start = time.monotonic()
            gate.set()
            t1.join(timeout=2)
            t2.join(timeout=2)
            elapsed = time.monotonic() - start
            self.assertFalse(t1.is_alive())
            self.assertFalse(t2.is_alive())
            self.assertFalse(errors)
            self.assertEqual(len(results), 2)
            self.assertLess(elapsed, 0.8)
            self.assertTrue(all(result.status == ActionStatus.FAILED for result in results))

    def test_internal_exception_never_returns_verified(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = self.action(core, session, "never-verified")
            core.registry.resolve = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("phase25-resolution-boom"))
            result = core.kernel.execute(action, session, core.confirm(action, session))
            self.assertNotEqual(result.status, ActionStatus.VERIFIED)
            self.assertFalse((root / "never-verified").exists())

    def test_internal_exception_preserves_conservative_accounting(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = self.action(core, session, "accounting")
            grant = core.grant_authority._get_for_test(session.principal_id, action.capability_id, action.operation, action.capability_version)

            def boom(*args, **kwargs):
                raise RuntimeError("phase25-lease-after-accounting-boom")

            core.kernel._ActionKernel__lease_issuer.issue = boom
            result = core.kernel.execute(action, session, core.confirm(action, session))
            self.assertEqual(result.status, ActionStatus.FAILED)
            self.assertEqual(core.kernel.grant_calls.used(grant.grant_id), 1)
            self.assertFalse((root / "accounting").exists())


class Phase25FilesystemBoundaryTests(unittest.TestCase):
    def make_core(self, root: Path, limits: ResourceLimits | None = None) -> ZorqCore:
        return ZorqCore(CoreConfig("owner-1", "device-1", "correct-secret", (root,), resource_limits=limits or ResourceLimits()), memory=TestMemoryOSAdapter())

    def inspect(self, core: ZorqCore, session, root: Path):
        action = core.build_action(session, "filesystem.approved", "inspect_directory", {"path": str(root)})
        return core.execute_action(action, session, core.confirm(action, session))

    def make_symlink_or_skip(self, target: Path, link: Path):
        try:
            link.symlink_to(target, target_is_directory=target.is_dir())
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink unavailable on this platform: {exc}")

    def test_directory_inspection_does_not_follow_symlink(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            outside = Path(tmp) / "outside.txt"; outside.write_text("secret", encoding="utf-8")
            link = root / "link-to-file"
            self.make_symlink_or_skip(outside, link)
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            result = self.inspect(core, session, root)
            self.assertEqual(result.status, ActionStatus.VERIFIED)
            entry = next(item for item in result.execution.data["entries"] if item["name"] == link.name)
            self.assertTrue(entry["is_symlink"])
            self.assertFalse(entry["is_file"])
            self.assertFalse(entry["is_dir"])

    def test_directory_inspection_does_not_reveal_outside_target_type(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            outside_dir = Path(tmp) / "outside-dir"; outside_dir.mkdir()
            link = root / "link-to-dir"
            self.make_symlink_or_skip(outside_dir, link)
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            result = self.inspect(core, session, root)
            self.assertEqual(result.status, ActionStatus.VERIFIED)
            entry = next(item for item in result.execution.data["entries"] if item["name"] == link.name)
            self.assertTrue(entry["is_symlink"])
            self.assertFalse(entry["is_dir"])
            self.assertFalse(entry["is_file"])

    def test_directory_inspection_handles_broken_symlink(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            link = root / "broken"
            try:
                link.symlink_to(Path(tmp) / "missing")
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlink unavailable on this platform: {exc}")
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            result = self.inspect(core, session, root)
            self.assertEqual(result.status, ActionStatus.VERIFIED)
            entry = next(item for item in result.execution.data["entries"] if item["name"] == link.name)
            self.assertTrue(entry["is_symlink"])
            self.assertFalse(entry["is_dir"])
            self.assertFalse(entry["is_file"])

    def test_directory_inspection_remains_within_root(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            outside = Path(tmp) / "outside"; outside.mkdir()
            link = root / "outside-link"
            self.make_symlink_or_skip(outside, link)
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            result = self.inspect(core, session, root)
            self.assertEqual(result.status, ActionStatus.VERIFIED)
            names = [item["name"] for item in result.execution.data["entries"]]
            self.assertEqual(names, ["outside-link"])

    def test_directory_limit_rejects_without_full_materialization(self):
        fake_dir = CountingDirectory(count=100, fail_after=4)
        agent = object.__new__(LocalDeviceAgent)
        agent.policy = SimpleNamespace(
            canonical_path=lambda raw: fake_dir,
            limits=ResourceLimits(max_directory_entries=3),
        )
        action = ActionRequest(
            action_id="a", task_id="t", owner_id="o", principal_id="o", device_id="d",
            capability_id="filesystem.approved", capability_version="1.0.0", operation="inspect_directory",
            parameters={"path": "/fake"}, purpose="test", risk=RiskLevel.R1,
            expected_effect="test", verification_requirement="test", idempotency_key="k", created_at=utc_now(), timeout_seconds=1,
        )
        with self.assertRaises(SecurityViolation):
            agent._inspect_directory(action)
        self.assertLessEqual(fake_dir.yielded, 4)

    def test_directory_limit_never_returns_more_than_manifest(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            for i in range(2):
                (root / f"item-{i}").write_text("x", encoding="utf-8")
            core = self.make_core(root, ResourceLimits(max_directory_entries=2))
            session = core.establish_owner_session("correct-secret")
            result = self.inspect(core, session, root)
            self.assertEqual(result.status, ActionStatus.VERIFIED)
            self.assertLessEqual(len(result.execution.data["entries"]), 2)

    def test_large_directory_fails_closed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            for i in range(4):
                (root / f"item-{i}").write_text("x", encoding="utf-8")
            core = self.make_core(root, ResourceLimits(max_directory_entries=3))
            session = core.establish_owner_session("correct-secret")
            result = self.inspect(core, session, root)
            self.assertEqual(result.status, ActionStatus.FAILED)
            self.assertIn("directory_entry_limit", result.execution.error)

    def test_directory_listing_order_is_deterministic_within_limit(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            for name in ["b.txt", "a.txt", "c.txt"]:
                (root / name).write_text("x", encoding="utf-8")
            core = self.make_core(root, ResourceLimits(max_directory_entries=3))
            session = core.establish_owner_session("correct-secret")
            result = self.inspect(core, session, root)
            self.assertEqual(result.status, ActionStatus.VERIFIED)
            self.assertEqual([entry["name"] for entry in result.execution.data["entries"]], ["a.txt", "b.txt", "c.txt"])

    def test_missing_o_nofollow_does_not_advertise_supported(self):
        with mock.patch.object(security_module.os, "supports_dir_fd", {security_module.os.mkdir, security_module.os.open}), \
             mock.patch.object(security_module.os, "O_NOFOLLOW", None, create=True):
            self.assertFalse(security_module._supports_dir_fd_mutation())

    def test_policy_reports_degraded_when_mutation_primitives_incomplete(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            with mock.patch("zroq.security._supports_dir_fd_mutation", return_value=False), \
                 mock.patch("zroq.security._supports_windows_mutation", return_value=False):
                policy = security_module.DeviceSecurityPolicy((root,))
            self.assertEqual(policy.posture, FileSystemPosture.DEGRADED)
            with self.assertRaises(SecurityViolation):
                policy.assert_mutation_supported()


if __name__ == "__main__":
    unittest.main()
