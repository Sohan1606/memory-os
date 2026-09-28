from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest

from zroq.contracts import ActionSnapshot, ActionStatus, ExecutionLease, action_snapshot, utc_now
from zroq.core import CoreConfig, ZorqCore
from zroq.memory import TestMemoryOSAdapter


class MutableMapping(dict):
    pass


class Phase26ActionSnapshotTests(unittest.TestCase):
    def make_core(self, root: Path) -> ZorqCore:
        return ZorqCore(CoreConfig("owner-1", "device-1", "correct-secret", (root,)), memory=TestMemoryOSAdapter())

    def build_file_action(self, core: ZorqCore, session, path: Path, content="authorized", params=None):
        parameters = params if params is not None else {"path": str(path), "content": content}
        return core.build_action(session, "filesystem.approved", "create_text_file", parameters)

    def test_action_snapshot_is_immutable(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            action = self.build_file_action(core, session, root / "a.txt")
            snapshot = action_snapshot(action)
            self.assertIsInstance(snapshot, ActionSnapshot)
            with self.assertRaises(Exception):
                snapshot.action_id = "mutated"
            with self.assertRaises(TypeError):
                snapshot.parameters["path"] = str(root / "b.txt")

    def test_action_nested_mapping_is_immutable(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            params = {"path": str(root / "a.txt"), "content": "x", "meta": {"nested": {"key": "value"}}}
            snapshot = action_snapshot(self.build_file_action(core, session, root / "a.txt", params=params))
            with self.assertRaises(TypeError):
                snapshot.parameters["meta"]["nested"]["key"] = "mutated"

    def test_action_nested_sequence_is_immutable(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            params = {"path": str(root / "a.txt"), "content": "x", "items": ["a", {"b": ["c"]}]}
            snapshot = action_snapshot(self.build_file_action(core, session, root / "a.txt", params=params))
            self.assertIsInstance(snapshot.parameters["items"], tuple)
            with self.assertRaises(AttributeError):
                snapshot.parameters["items"].append("mutated")
            with self.assertRaises(TypeError):
                snapshot.parameters["items"][1]["b"][0] = "mutated"

    def test_original_parameters_cannot_mutate_snapshot(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            params = {"path": str(root / "a.txt"), "content": "x", "nested": {"value": "safe"}, "items": ["safe"]}
            snapshot = action_snapshot(self.build_file_action(core, session, root / "a.txt", params=params))
            params["path"] = str(root / "b.txt")
            params["nested"]["value"] = "mutated"
            params["items"].append("mutated")
            self.assertEqual(snapshot.parameters["path"], str(root / "a.txt"))
            self.assertEqual(snapshot.parameters["nested"]["value"], "safe")
            self.assertEqual(snapshot.parameters["items"], ("safe",))

    def test_snapshot_has_no_mutable_nested_alias(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            nested = {"value": "safe"}
            items = ["safe", {"deep": "safe"}]
            params = MutableMapping({"path": str(root / "a.txt"), "content": "x", "nested": nested, "items": items})
            snapshot = action_snapshot(self.build_file_action(core, session, root / "a.txt", params=params))
            self.assertNotEqual(id(snapshot.parameters), id(params))
            self.assertNotEqual(id(snapshot.parameters["nested"]), id(nested))
            self.assertNotEqual(id(snapshot.parameters["items"]), id(items))
            with self.assertRaises(TypeError):
                snapshot.parameters["items"][1]["deep"] = "mutated"

    def test_parameter_mutation_after_authorization_executes_snapshot(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            file_a = root / "authorized-A.txt"
            file_b = root / "mutated-B.txt"
            action = self.build_file_action(core, session, file_a)
            entered_issue = threading.Event()
            continue_issue = threading.Event()
            original_issue = core.kernel._ActionKernel__lease_issuer.issue

            def delayed_issue(*args, **kwargs):
                entered_issue.set()
                self.assertTrue(continue_issue.wait(timeout=5))
                return original_issue(*args, **kwargs)

            core.kernel._ActionKernel__lease_issuer.issue = delayed_issue
            result_box = {}
            thread = threading.Thread(target=lambda: result_box.setdefault("result", core.execute_action(action, session, core.confirm(action, session))))
            thread.start()
            self.assertTrue(entered_issue.wait(timeout=5))
            action.parameters["path"] = str(file_b)
            action.parameters["content"] = "mutated"
            continue_issue.set()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(result_box["result"].status, ActionStatus.VERIFIED)
            self.assertEqual(file_a.read_text(encoding="utf-8"), "authorized")
            self.assertFalse(file_b.exists())

    def test_parameter_mutation_after_lease_issuance_executes_snapshot(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            file_a = root / "authorized-A.txt"
            file_b = root / "mutated-B.txt"
            action = self.build_file_action(core, session, file_a)
            entered_execute = threading.Event()
            continue_execute = threading.Event()
            original_execute = core.device_agent.execute

            def delayed_execute(snapshot, cancel_event, lease):
                entered_execute.set()
                self.assertTrue(continue_execute.wait(timeout=5))
                return original_execute(snapshot, cancel_event, lease)

            core.device_agent.execute = delayed_execute
            result_box = {}
            thread = threading.Thread(target=lambda: result_box.setdefault("result", core.execute_action(action, session, core.confirm(action, session))))
            thread.start()
            self.assertTrue(entered_execute.wait(timeout=5))
            action.parameters["path"] = str(file_b)
            action.parameters["content"] = "mutated"
            continue_execute.set()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(result_box["result"].status, ActionStatus.VERIFIED)
            self.assertEqual(file_a.read_text(encoding="utf-8"), "authorized")
            self.assertFalse(file_b.exists())

    def test_mutation_after_lease_verification_does_not_change_execution_target(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            file_a = root / "authorized-A.txt"
            file_b = root / "mutated-B.txt"
            action = self.build_file_action(core, session, file_a)
            entered_device_handler = threading.Event()
            continue_execution = threading.Event()
            original_handler = core.device_agent._create_text_file

            def delayed_handler(snapshot, cancel_event, lease):
                self.assertIsInstance(snapshot, ActionSnapshot)
                entered_device_handler.set()
                self.assertTrue(continue_execution.wait(timeout=5))
                return original_handler(snapshot, cancel_event, lease)

            core.device_agent._create_text_file = delayed_handler
            result_box = {}
            thread = threading.Thread(target=lambda: result_box.setdefault("result", core.execute_action(action, session, core.confirm(action, session))))
            thread.start()
            self.assertTrue(entered_device_handler.wait(timeout=5))
            action.parameters["path"] = str(file_b)
            action.parameters["content"] = "mutated"
            continue_execution.set()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(result_box["result"].status, ActionStatus.VERIFIED)
            self.assertEqual(file_a.read_text(encoding="utf-8"), "authorized")
            self.assertFalse(file_b.exists())

    def test_confirmation_for_a_does_not_authorize_mutated_b(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            file_a = root / "authorized-A.txt"
            file_b = root / "mutated-B.txt"
            action = self.build_file_action(core, session, file_a)
            confirmation = core.confirm(action, session)
            action.parameters["path"] = str(file_b)
            action.parameters["content"] = "mutated"
            result = core.execute_action(action, session, confirmation)
            self.assertEqual(result.status, ActionStatus.AUTHORIZATION_REQUIRED)
            self.assertFalse(file_a.exists())
            self.assertFalse(file_b.exists())

    def test_idempotent_replay_uses_same_snapshot_and_changed_snapshot_conflicts(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            file_a = root / "authorized-A.txt"
            file_b = root / "mutated-B.txt"
            action = self.build_file_action(core, session, file_a)
            confirmation = core.confirm(action, session)
            first = core.execute_action(action, session, confirmation)
            replay = core.execute_action(action, session, confirmation)
            self.assertEqual(first.status, ActionStatus.VERIFIED)
            self.assertEqual(replay.status, ActionStatus.VERIFIED)
            action.parameters["path"] = str(file_b)
            action.parameters["content"] = "mutated"
            conflict = core.execute_action(action, session, confirmation)
            self.assertEqual(conflict.status, ActionStatus.FAILED)
            self.assertEqual(conflict.evidence.get("reason"), "idempotency_conflict")
            self.assertFalse(file_b.exists())

    def test_replacing_parameters_dict_after_snapshot_does_not_affect_execution(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            file_a = root / "authorized-A.txt"
            file_b = root / "mutated-B.txt"
            action = self.build_file_action(core, session, file_a)
            entered_device_handler = threading.Event()
            continue_execution = threading.Event()
            original_handler = core.device_agent._create_text_file

            def delayed_handler(snapshot, cancel_event, lease):
                entered_device_handler.set()
                self.assertTrue(continue_execution.wait(timeout=5))
                return original_handler(snapshot, cancel_event, lease)

            core.device_agent._create_text_file = delayed_handler
            result_box = {}
            thread = threading.Thread(target=lambda: result_box.setdefault("result", core.execute_action(action, session, core.confirm(action, session))))
            thread.start()
            self.assertTrue(entered_device_handler.wait(timeout=5))
            object.__setattr__(action, "parameters", {"path": str(file_b), "content": "mutated"})
            continue_execution.set()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(result_box["result"].status, ActionStatus.VERIFIED)
            self.assertTrue(file_a.exists())
            self.assertFalse(file_b.exists())

    def test_nested_content_list_mutation_after_snapshot_does_not_affect_execution(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            file_a = root / "authorized-A.txt"
            content_list = ["authorized"]
            action = self.build_file_action(core, session, file_a, params={"path": str(file_a), "content": content_list})
            entered_device_handler = threading.Event()
            continue_execution = threading.Event()
            original_handler = core.device_agent._create_text_file

            def delayed_handler(snapshot, cancel_event, lease):
                entered_device_handler.set()
                self.assertTrue(continue_execution.wait(timeout=5))
                return original_handler(snapshot, cancel_event, lease)

            core.device_agent._create_text_file = delayed_handler
            result_box = {}
            thread = threading.Thread(target=lambda: result_box.setdefault("result", core.execute_action(action, session, core.confirm(action, session))))
            thread.start()
            self.assertTrue(entered_device_handler.wait(timeout=5))
            content_list.append("mutated")
            continue_execution.set()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(result_box["result"].status, ActionStatus.VERIFIED)
            self.assertEqual(file_a.read_text(encoding="utf-8"), "('authorized',)")

    def test_device_agent_rejects_direct_mutable_action_request(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            core = self.make_core(root)
            session = core.establish_owner_session("correct-secret")
            file_a = root / "direct-A.txt"
            action = self.build_file_action(core, session, file_a)
            issued = utc_now()
            forged = ExecutionLease(
                lease_id="direct-mutable-action",
                action_id=action.action_id,
                action_digest=action.digest(),
                owner_id=action.owner_id,
                principal_id=action.principal_id,
                device_id=action.device_id,
                capability_id=action.capability_id,
                capability_version=action.capability_version,
                operation=action.operation,
                issued_at=issued,
                expires_at=issued + timedelta(seconds=5),
                issuer_id="zorq-action-kernel",
                policy_version=core.authority.policy_version,
                security_epoch=core.security_epoch.current(),
                issuer_signature="not-a-valid-signature",
            )
            observation = core.device_agent.execute(action, threading.Event(), forged)
            self.assertFalse(observation.executed)
            self.assertFalse(observation.effect_started)
            self.assertEqual(observation.error, "lease_requires_immutable_action_snapshot")
            self.assertFalse(file_a.exists())


if __name__ == "__main__":
    unittest.main()
