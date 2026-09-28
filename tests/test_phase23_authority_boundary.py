from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest

from zroq.contracts import ActionStatus, RiskLevel, action_snapshot, utc_now
from zroq.core import CoreConfig, ZorqCore
from zroq.memory import TestMemoryOSAdapter, UnavailableMemoryOSAdapter
from zroq.security import ResourceLimits


class Phase23AuthorityBoundaryTests(unittest.TestCase):
    def make_core(self, root: Path, memory=None) -> ZorqCore:
        return ZorqCore(CoreConfig("owner-1", "device-1", "correct-secret", (root,)), memory=memory or TestMemoryOSAdapter())

    def assert_rejected_without_effect(self, result, target: Path) -> None:
        self.assertNotEqual(result.status, ActionStatus.VERIFIED)
        self.assertFalse(target.exists())

    def test_kernel_rejects_unregistered_capability(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            target = root / "unregistered"
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(target)})
            hostile = replace(action, capability_id="evil.capability", capability_version="1.0.0", risk=RiskLevel.R0)
            result = core.kernel.execute(hostile, session)
            self.assertEqual(result.status, ActionStatus.DENIED)
            self.assertIn("capability", result.message)
            self.assertFalse(target.exists())

    def test_kernel_does_not_accept_caller_supplied_forged_manifest(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root, memory=UnavailableMemoryOSAdapter())
            session = core.establish_owner_session("correct-secret")
            target = root / "forged-manifest"
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(target)})
            hostile = replace(action, risk=RiskLevel.R0)
            result = core.kernel.execute(hostile, session)
            self.assertEqual(result.status, ActionStatus.DENIED)
            self.assertIn("action_risk_mismatch", result.message)
            self.assertFalse(target.exists())

    def test_kernel_does_not_accept_caller_supplied_forged_grant(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            target = root / "forged-grant"
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(target)})
            core.grant_authority._remove_for_test(session.principal_id, action.capability_id, action.operation, action.capability_version)
            result = core.kernel.execute(action, session, core.confirm(action, session))
            self.assertEqual(result.status, ActionStatus.DENIED)
            self.assertIn("grant_not_found", result.message)
            self.assertFalse(target.exists())

    def test_forged_manifest_cannot_lower_risk(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            target = root / "lower-risk"
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(target)})
            hostile = replace(action, risk=RiskLevel.R0)
            result = core.kernel.execute(hostile, session)
            self.assertEqual(result.status, ActionStatus.DENIED)
            self.assertIn("action_risk_mismatch", result.message)
            self.assertFalse(target.exists())

    def test_forged_manifest_cannot_remove_confirmation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            target = root / "no-confirmation"
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(target)})
            result = core.kernel.execute(action, session)
            self.assertEqual(result.status, ActionStatus.AUTHORIZATION_REQUIRED)
            self.assertIn("confirmation_required", result.message)
            self.assertFalse(target.exists())

    def test_forged_manifest_cannot_disable_memory_governance(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root, memory=UnavailableMemoryOSAdapter())
            session = core.establish_owner_session("correct-secret")
            target = root / "no-governance"
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(target)})
            result = core.kernel.execute(action, session, core.confirm(action, session))
            self.assertEqual(result.status, ActionStatus.DENIED)
            self.assertIn("memoryos_governance_unavailable", result.message)
            self.assertFalse(target.exists())

    def test_forged_grant_cannot_expand_scope(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            outside = Path(tmp) / "outside"; outside.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            target = outside / "expanded-scope"
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(target)})
            result = core.kernel.execute(action, session, core.confirm(action, session))
            self.assertEqual(result.status, ActionStatus.DENIED)
            self.assertIn("path_outside_grant", result.message)
            self.assertFalse(target.exists())

    def test_forged_grant_cannot_increase_call_budget(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            first = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "first")})
            self.assertEqual(core.execute_action(first, session, core.confirm(first, session)).status, ActionStatus.VERIFIED)
            second = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "second")})
            result = core.kernel.execute(second, session, core.confirm(second, session))
            self.assertEqual(result.status, ActionStatus.DENIED)
            self.assertIn("grant_max_calls_exhausted", result.message)
            self.assertFalse((root / "second").exists())

    def test_registered_capability_version_is_authoritative(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            target = root / "wrong-version"
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(target)})
            hostile = replace(action, capability_version="forged-version")
            result = core.kernel.execute(hostile, session, core.confirm(hostile, session))
            self.assertEqual(result.status, ActionStatus.DENIED)
            self.assertIn("version", result.message)
            self.assertFalse(target.exists())

    def test_kernel_resolves_manifest_from_authoritative_registry(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root, memory=UnavailableMemoryOSAdapter())
            session = core.establish_owner_session("correct-secret")
            target = root / "registry-source"
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(target)})
            result = core.kernel.execute(action, session, core.confirm(action, session))
            self.assertEqual(result.status, ActionStatus.DENIED)
            self.assertIn("memoryos_governance", result.message)
            self.assertFalse(target.exists())

    def test_manifest_nested_mapping_is_immutable(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            manifest = core.registry.resolve("filesystem.approved").manifest
            with self.assertRaises(TypeError):
                manifest.input_schema["path"] = "changed"
            with self.assertRaises(TypeError):
                manifest.output_schema["path"] = "changed"

    def test_sealed_registry_manifest_cannot_be_modified(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            manifest = core.registry.all_manifests()[0]
            with self.assertRaises(TypeError):
                manifest.resource_limits["max_output_bytes"] = 1

    def test_resource_limits_cannot_be_changed_after_seal(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            manifest = core.registry.resolve("filesystem.approved").manifest
            with self.assertRaises(TypeError):
                manifest.resource_limits["max_file_bytes"] = 999999999

    def test_schema_cannot_be_changed_after_seal(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            manifest = core.registry.resolve("filesystem.approved").manifest
            with self.assertRaises(TypeError):
                manifest.input_schema["extra"] = "evil"

    def test_authoritative_grant_version_mismatch_fails(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "bad-version")})
            bad = replace(action, capability_version="0.0.0")
            result = core.kernel.execute(bad, session, core.confirm(bad, session))
            self.assertNotEqual(result.status, ActionStatus.VERIFIED)
            self.assertFalse((root / "bad-version").exists())

    def test_inactive_authoritative_grant_fails(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "inactive")})
            grant = core.grant_authority._get_for_test(session.principal_id, action.capability_id, action.operation, action.capability_version)
            core.grant_authority._replace_for_test(replace(grant, active=False))
            result = core.kernel.execute(action, session, core.confirm(action, session))
            self.assertEqual(result.status, ActionStatus.DENIED)
            self.assertFalse((root / "inactive").exists())

    def test_expired_authoritative_grant_fails(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "expired")})
            grant = core.grant_authority._get_for_test(session.principal_id, action.capability_id, action.operation, action.capability_version)
            core.grant_authority._replace_for_test(replace(grant, expires_at=utc_now() - timedelta(seconds=1)))
            result = core.kernel.execute(action, session, core.confirm(action, session))
            self.assertEqual(result.status, ActionStatus.DENIED)
            self.assertFalse((root / "expired").exists())

    def test_authoritative_grant_manifest_digest_mismatch_fails(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "digest")})
            grant = core.grant_authority._get_for_test(session.principal_id, action.capability_id, action.operation, action.capability_version)
            core.grant_authority._replace_for_test(replace(grant, manifest_digest="forged-digest"))
            result = core.kernel.execute(action, session, core.confirm(action, session))
            self.assertEqual(result.status, ActionStatus.DENIED)
            self.assertFalse((root / "digest").exists())

    def test_device_agent_rejects_unsupported_capability_table_entry(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "local.time", "read_current_time", {})
            unsupported = action_snapshot(replace(action, capability_version="9.9.9"))
            issued = utc_now()
            lease = core.kernel._ActionKernel__lease_issuer.issue(
                unsupported,
                lease_id="phase23-unsupported-table-entry",
                issued_at=issued,
                expires_at=issued + timedelta(seconds=5),
                policy_version=core.authority.policy_version,
                security_epoch=core.security_epoch.current(),
                max_calls=1,
            )
            observation = core.device_agent.execute(unsupported, threading.Event(), lease)
            self.assertFalse(observation.executed)
            self.assertFalse(observation.effect_started)
            self.assertIn("trusted_device_capability_table", observation.error)

    def test_effective_resource_limit_does_not_exceed_device_ceiling(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = ZorqCore(
                CoreConfig(
                    "owner-1",
                    "device-1",
                    "correct-secret",
                    (root,),
                    resource_limits=ResourceLimits(max_output_bytes=16),
                ),
                memory=TestMemoryOSAdapter(),
            )
            self.assertEqual(core.device_policy.limits.max_output_bytes, 16)
            session = core.establish_owner_session("correct-secret")
            target = root / "too-large.txt"
            target.write_text("x" * 32, encoding="utf-8")
            action = core.build_action(session, "filesystem.approved", "read_text_file", {"path": str(target)})
            result = core.execute_action(action, session, core.confirm(action, session))
            self.assertNotEqual(result.status, ActionStatus.VERIFIED)
            self.assertIn(result.status, {ActionStatus.FAILED, ActionStatus.UNKNOWN})

    def test_audit_records_authority_denial_and_chain_remains_valid(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "audit-denied")})
            denied = replace(action, capability_id="evil.capability", capability_version="1.0.0", risk=RiskLevel.R0)
            result = core.kernel.execute(denied, session)
            self.assertEqual(result.status, ActionStatus.DENIED)
            self.assertTrue(core.verify_audit())
            self.assertFalse((root / "audit-denied").exists())


if __name__ == "__main__":
    unittest.main()
