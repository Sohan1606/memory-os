from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dataclasses import replace
from datetime import timedelta
import inspect
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from zroq.action_kernel import ActionKernel
from zroq.capabilities import CapabilityManifest, CapabilityRegistry
from zroq.contracts import ActionStatus, CapabilityState, ConfirmationMode, RiskLevel, utc_now
from zroq.core import CoreConfig, ZorqCore
from zroq.memory import TestMemoryOSAdapter
from zroq.security import ResourceLimits


class Phase24ContractIntegrityTests(unittest.TestCase):
    def make_core(self, root: Path, *, limits: ResourceLimits | None = None) -> ZorqCore:
        return ZorqCore(
            CoreConfig("owner-1", "device-1", "correct-secret", (root,), resource_limits=limits or ResourceLimits()),
            memory=TestMemoryOSAdapter(),
        )

    def capture_next_lease(self, core: ZorqCore) -> dict:
        captured: dict = {}
        original = core.device_agent.execute

        def wrapped(action, cancel_event, lease):
            captured["lease"] = lease
            return original(action, cancel_event, lease)

        core.device_agent.execute = wrapped
        return captured

    def build_nested_manifest(self, input_schema=None, output_schema=None, resource_limits=None) -> CapabilityManifest:
        return CapabilityManifest(
            capability_id="test.deep.freeze",
            version="1.0.0",
            operations=("op",),
            permissions=("test.deep.freeze",),
            risk=RiskLevel.R0,
            input_schema=input_schema or {"outer": {"inner": "safe"}},
            output_schema=output_schema or {"outer": {"inner": "safe-output"}},
            cancellation="none",
            verification="test-verification",
            audit_events=("test",),
            failure_behavior="REPORT_FAILED",
            timeout_seconds=1,
            resource_limits=resource_limits or {"max_output_bytes": 100, "nested": {"max": 1}},
            state=CapabilityState.ENABLED,
            requires_memory_governance=False,
            confirmation_mode=ConfirmationMode.NONE,
            description="test manifest",
        )

    def registered_test_manifest(self, manifest: CapabilityManifest) -> CapabilityManifest:
        registry = CapabilityRegistry()
        registry.register(manifest)
        registry.seal()
        return registry.resolve(manifest.capability_id).manifest

    def test_current_kernel_execute_api_shape(self):
        signature = inspect.signature(ActionKernel.execute)
        self.assertEqual(list(signature.parameters), ["self", "action", "session", "confirmation"])
        self.assertEqual(signature.parameters["confirmation"].default, None)
        self.assertNotIn("manifest", signature.parameters)
        self.assertNotIn("grant", signature.parameters)

    def test_authority_objects_cannot_be_caller_selected_by_normal_api(self):
        signature = inspect.signature(ActionKernel.execute)
        parameter_names = set(signature.parameters)
        self.assertFalse({"manifest", "grant", "capability_manifest", "permission_grant"} & parameter_names)

    def test_action_timeout_cannot_exceed_manifest_timeout(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            target = root / "large-timeout"
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(target)})
            hostile = replace(action, timeout_seconds=3600)
            captured = self.capture_next_lease(core)
            result = core.execute_action(hostile, session, core.confirm(hostile, session))
            self.assertEqual(result.status, ActionStatus.VERIFIED)
            lease = captured["lease"]
            lifetime = (lease.expires_at - lease.issued_at).total_seconds()
            manifest = core.registry.resolve("filesystem.approved").manifest
            self.assertLessEqual(lifetime, manifest.timeout_seconds)
            self.assertTrue(target.exists())

    def test_lease_expiry_uses_authoritative_timeout_ceiling(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            target = root / "lease-ceiling"
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(target)})
            hostile = replace(action, timeout_seconds=3600)
            captured = self.capture_next_lease(core)
            result = core.execute_action(hostile, session, core.confirm(hostile, session))
            self.assertEqual(result.status, ActionStatus.VERIFIED)
            lease = captured["lease"]
            manifest = core.registry.resolve("filesystem.approved").manifest
            self.assertLessEqual(lease.expires_at, lease.issued_at + timedelta(seconds=manifest.timeout_seconds))
            self.assertTrue(target.exists())

    def test_shorter_caller_timeout_is_not_expanded(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            target = root / "short-timeout"
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(target)})
            shorter = replace(action, timeout_seconds=1.0)
            captured = self.capture_next_lease(core)
            result = core.execute_action(shorter, session, core.confirm(shorter, session))
            self.assertEqual(result.status, ActionStatus.VERIFIED)
            lease = captured["lease"]
            lifetime = (lease.expires_at - lease.issued_at).total_seconds()
            self.assertLessEqual(lifetime, 1.0)
            self.assertTrue(target.exists())

    def test_large_caller_timeout_cannot_extend_session_or_manifest_bound(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            short_session = replace(session, expires_at=utc_now() + timedelta(seconds=2))
            core.sessions._sessions[session.session_id] = short_session
            target = root / "session-bound"
            action = core.build_action(short_session, "filesystem.approved", "create_directory", {"path": str(target)})
            hostile = replace(action, timeout_seconds=3600)
            captured = self.capture_next_lease(core)
            result = core.execute_action(hostile, short_session, core.confirm(hostile, short_session))
            self.assertEqual(result.status, ActionStatus.VERIFIED)
            lease = captured["lease"]
            lifetime = (lease.expires_at - lease.issued_at).total_seconds()
            self.assertLessEqual(lifetime, 2.0)
            self.assertLessEqual(lifetime, core.registry.resolve("filesystem.approved").manifest.timeout_seconds)
            self.assertTrue(target.exists())

    def test_manifest_deep_mapping_is_immutable(self):
        stored = self.registered_test_manifest(self.build_nested_manifest())
        with self.assertRaises(TypeError):
            stored.input_schema["outer"]["inner"] = "mutated"
        with self.assertRaises(TypeError):
            stored.output_schema["outer"]["inner"] = "mutated"

    def test_manifest_nested_sequence_is_immutable(self):
        stored = self.registered_test_manifest(
            self.build_nested_manifest(input_schema={"sequence": ["a", {"b": ["c"]}]})
        )
        self.assertIsInstance(stored.input_schema["sequence"], tuple)
        with self.assertRaises(AttributeError):
            stored.input_schema["sequence"].append("mutated")
        with self.assertRaises(TypeError):
            stored.input_schema["sequence"][1]["b"][0] = "mutated"

    def test_registered_manifest_has_no_mutable_nested_alias(self):
        nested = {"outer": {"inner": "safe"}, "sequence": ["safe", {"deep": "safe"}], "set": {"a", "b"}}
        stored = self.registered_test_manifest(self.build_nested_manifest(input_schema=nested))
        self.assertNotEqual(id(stored.input_schema["outer"]), id(nested["outer"]))
        self.assertIsInstance(stored.input_schema["sequence"], tuple)
        self.assertIsInstance(stored.input_schema["set"], frozenset)

    def test_original_manifest_input_cannot_mutate_registered_copy(self):
        source_input = {"outer": {"inner": "safe"}, "sequence": ["safe", {"deep": "safe"}]}
        source_limits = {"max_output_bytes": 100, "nested": {"max": 1}, "seq": [1, {"inner": 2}]}
        stored = self.registered_test_manifest(self.build_nested_manifest(input_schema=source_input, resource_limits=source_limits))
        source_input["outer"]["inner"] = "mutated-source"
        source_input["sequence"].append("mutated-source-list")
        source_limits["nested"]["max"] = 999
        source_limits["seq"][1]["inner"] = 777
        self.assertEqual(stored.input_schema["outer"]["inner"], "safe")
        self.assertEqual(stored.input_schema["sequence"], ("safe", stored.input_schema["sequence"][1]))
        self.assertEqual(stored.resource_limits["nested"]["max"], 1)
        self.assertEqual(stored.resource_limits["seq"][1]["inner"], 2)

    def test_nested_resource_limit_cannot_be_changed(self):
        stored = self.registered_test_manifest(self.build_nested_manifest(resource_limits={"nested": {"max": 1}, "seq": [1, {"inner": 2}]}))
        with self.assertRaises(TypeError):
            stored.resource_limits["nested"]["max"] = 999
        with self.assertRaises(TypeError):
            stored.resource_limits["seq"][1]["inner"] = 999

    def test_hostile_action_fields_are_validated_against_authoritative_policy(self):
        hostile_cases = (
            ("wrong_version", lambda a: replace(a, capability_version="9.9.9"), "version"),
            ("wrong_operation", lambda a: replace(a, operation="unsupported_operation"), "operation"),
            ("downgraded_risk", lambda a: replace(a, risk=RiskLevel.R0), "risk"),
            ("altered_verification", lambda a: replace(a, verification_requirement="forged-verification"), "verification"),
            ("altered_expected_effect", lambda a: replace(a, expected_effect="forged expected effect"), "expected_effect"),
        )
        for name, mutate, expected_message in hostile_cases:
            with self.subTest(name=name), TemporaryDirectory() as tmp:
                root = Path(tmp) / "approved"; root.mkdir()
                core = self.make_core(root)
                session = core.establish_owner_session("correct-secret")
                target = root / name
                action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(target)})
                hostile = mutate(action)
                confirmation = core.confirm(hostile, session)
                result = core.kernel.execute(hostile, session, confirmation)
                self.assertEqual(result.status, ActionStatus.DENIED)
                self.assertIn(expected_message, result.message)
                self.assertFalse(target.exists())

    def test_unknown_capability_hostile_request_is_denied_without_side_effect(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            target = root / "unknown-capability"
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(target)})
            hostile = replace(action, capability_id="unknown.capability", capability_version="1.0.0", risk=RiskLevel.R0)
            result = core.kernel.execute(hostile, session)
            self.assertEqual(result.status, ActionStatus.DENIED)
            self.assertFalse(target.exists())

    def test_forged_purpose_is_denied_when_grant_binds_purpose(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            target = root / "purpose-bound"
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(target)}, purpose="trusted-purpose")
            grant = core.grant_authority._get_for_test(session.principal_id, action.capability_id, action.operation, action.capability_version)
            core.grant_authority._replace_for_test(replace(grant, purpose="trusted-purpose"))
            hostile = replace(action, purpose="forged-purpose")
            result = core.kernel.execute(hostile, session, core.confirm(hostile, session))
            self.assertEqual(result.status, ActionStatus.DENIED)
            self.assertIn("purpose_mismatch", result.message)
            self.assertFalse(target.exists())

    def test_direct_grant_installation_after_seal_is_denied(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(root / "unused")})
            grant = core.grant_authority._get_for_test(session.principal_id, action.capability_id, action.operation, action.capability_version)
            with self.assertRaises(PermissionError):
                core.grant_authority.install(grant)

    def test_directory_entry_limit_fails_closed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            for i in range(3):
                (root / f"item-{i}").write_text("x", encoding="utf-8")
            core = self.make_core(root, limits=ResourceLimits(max_directory_entries=2))
            session = core.establish_owner_session("correct-secret")
            action = core.build_action(session, "filesystem.approved", "inspect_directory", {"path": str(root)})
            result = core.execute_action(action, session, core.confirm(action, session))
            self.assertEqual(result.status, ActionStatus.FAILED)
            self.assertIn("directory_entry_limit", result.execution.error)

    def test_max_path_length_is_enforced(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root, limits=ResourceLimits(max_path_length=20))
            session = core.establish_owner_session("correct-secret")
            too_long = root / ("x" * 100)
            action = core.build_action(session, "filesystem.approved", "create_directory", {"path": str(too_long)})
            result = core.execute_action(action, session, core.confirm(action, session))
            self.assertNotEqual(result.status, ActionStatus.VERIFIED)
            self.assertFalse(too_long.exists())

    def test_create_text_file_max_file_bytes_is_enforced(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root, limits=ResourceLimits(max_file_bytes=4))
            session = core.establish_owner_session("correct-secret")
            target = root / "too-large.txt"
            action = core.build_action(session, "filesystem.approved", "create_text_file", {"path": str(target), "content": "12345"})
            result = core.execute_action(action, session, core.confirm(action, session))
            self.assertEqual(result.status, ActionStatus.FAILED)
            self.assertIn("content_size_limit", result.execution.error)
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
