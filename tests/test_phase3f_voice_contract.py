"""ZORQ Phase 3F (3F-min) voice authorization/confirmation boundary tests.

These tests prove, against the REAL contracts and runtime code paths, that:

  * a transcript ("spoken words") is ordinary untrusted user input;
  * spoken confirmation words cannot become a Confirmation record;
  * confirmation remains digest-bound to the immutable ActionSnapshot;
  * the conversation runtime has no route into the Action Plane;
  * the voice transcription module has no route into identity/sessions;
  * STOP(target=SPEECH) is a distinct, explicit control that never implies
    action cancellation, and a generic STOP without a target fails closed;
  * voice-equivalent text receives byte-identical classification to typed
    text because the runtime has no modality parameter at all.

Nothing here is simulated: every assertion exercises the actual modules.
Spec: docs/zorq/ZORQ-PHASE3F-VOICE-SPECIFICATION-v1.md §§8, 10, 12, 13, 26.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from datetime import timedelta
import inspect
import tempfile
import unittest

from zroq.confirmation import issue_confirmation
from zroq.contracts import (
    ActionRequest,
    AssuranceLevel,
    Confirmation,
    PrincipalType,
    RiskLevel,
    Session,
    action_snapshot,
    utc_now,
)
from zroq.conversation_runtime import (
    ConversationRuntime,
    DeterministicConversationProvider,
    RuntimeCommandKind,
)
from zroq.domain_contracts import (
    ContractValidationError,
    InteractionCommandType,
    InteractionControlCommand,
    InteractionTarget,
)
from zroq.personal_continuity import (
    DocumentedMemoryOSAdapter,
    MemoryAccessContext,
    MemoryCapturePolicyEngine,
    PersonalContinuityEngine,
    PersonalContinuityStore,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def make_session(owner: str = "owner-3f") -> Session:
    now = utc_now()
    return Session(
        session_id="sess-3f-1",
        owner_id=owner,
        principal_id=f"principal-{owner}",
        principal_type=PrincipalType.OWNER,
        device_id="device-3f-1",
        assurance=AssuranceLevel.A3,
        issued_at=now,
        expires_at=now + timedelta(minutes=10),
    )


def make_action(owner: str = "owner-3f", parameters: dict | None = None) -> ActionRequest:
    return ActionRequest(
        action_id="act-3f-1",
        task_id="task-3f-1",
        owner_id=owner,
        principal_id=f"principal-{owner}",
        device_id="device-3f-1",
        capability_id="filesystem.approved",
        capability_version="1.0",
        operation="write",
        parameters=parameters or {"path": "notes.txt", "content": "hello"},
        purpose="phase 3f confirmation-binding test",
        risk=RiskLevel.R3,
        expected_effect="file written",
        verification_requirement="file exists with content",
        idempotency_key="idem-3f-1",
        created_at=utc_now(),
    )


class TranscriptIsNotConfirmationTests(unittest.TestCase):
    """Spoken 'yes' != high-assurance confirmation (spec §13/HC1)."""

    def test_no_string_path_into_confirmation(self):
        """issue_confirmation requires an authenticated Session object; a
        transcript string has no route in — the API contract itself refuses."""
        signature = inspect.signature(issue_confirmation)
        self.assertEqual(
            signature.parameters["session"].annotation, "Session",
            "confirmation issuance must be bound to a Session, never text",
        )
        action = make_action()
        with self.assertRaises(AttributeError):
            # A transcript is just a string. It has no session validity, no
            # principal, no assurance — the call fails before any record is
            # produced. There is no overload that accepts text.
            issue_confirmation(action, "yes")  # type: ignore[arg-type]

    def test_confirmation_requires_valid_session(self):
        expired = Session(
            session_id="sess-3f-expired",
            owner_id="owner-3f",
            principal_id="principal-owner-3f",
            principal_type=PrincipalType.OWNER,
            device_id="device-3f-1",
            assurance=AssuranceLevel.A3,
            issued_at=utc_now() - timedelta(hours=2),
            expires_at=utc_now() - timedelta(hours=1),
        )
        with self.assertRaises(ValueError):
            issue_confirmation(make_action(), expired)

    def test_confirmation_is_digest_bound_to_snapshot(self):
        """Confirmation for action A cannot approve mutated action B, whether
        checked against the request or its immutable ActionSnapshot."""
        session = make_session()
        action_a = make_action()
        confirmation = issue_confirmation(action_a, session)

        self.assertTrue(confirmation.is_valid_for(action_a, session))
        self.assertTrue(confirmation.is_valid_for(action_snapshot(action_a), session))

        # Mutated parameters => different digest => confirmation invalid.
        action_b = make_action(parameters={"path": "notes.txt", "content": "EVIL"})
        self.assertNotEqual(action_a.digest(), action_b.digest())
        self.assertFalse(confirmation.is_valid_for(action_b, session))
        self.assertFalse(confirmation.is_valid_for(action_snapshot(action_b), session))

    def test_confirmation_is_session_bound(self):
        """A confirmation replayed on a different session is invalid — a
        recorded/replayed 'yes' from another context grants nothing."""
        session = make_session()
        other = Session(
            session_id="sess-3f-other",
            owner_id="owner-3f",
            principal_id="principal-owner-3f",
            principal_type=PrincipalType.OWNER,
            device_id="device-3f-2",
            assurance=AssuranceLevel.A3,
            issued_at=utc_now(),
            expires_at=utc_now() + timedelta(minutes=10),
        )
        action = make_action()
        confirmation = issue_confirmation(action, session)
        self.assertFalse(confirmation.is_valid_for(action, other))

    def test_kernel_confirmation_parameter_is_typed_not_text(self):
        """The Action Kernel's execute() accepts Confirmation | None — there
        is no textual confirmation channel anywhere in the pipeline."""
        from zroq.action_kernel import ActionKernel

        signature = inspect.signature(ActionKernel.execute)
        annotation = str(signature.parameters["confirmation"].annotation)
        self.assertIn("Confirmation", annotation)
        self.assertNotIn("str", annotation)


class VoiceCannotReachAuthorityTests(unittest.TestCase):
    """Static boundary proofs: the modules a transcript flows through have
    no imports into identity, sessions, or the Action Plane (spec §12/A1-A2).
    """

    FORBIDDEN_RUNTIME_IMPORTS = (
        "action_kernel", "authority", "grants", "capabilities",
        "confirmation", "leases", "identity", "device_agent",
        "verification", "audit",
    )

    def test_conversation_runtime_has_no_action_plane_imports(self):
        source = (REPO_ROOT / "src" / "zroq" / "conversation_runtime.py").read_text(encoding="utf-8")
        import_lines = [
            line.strip() for line in source.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        for line in import_lines:
            for module in self.FORBIDDEN_RUNTIME_IMPORTS:
                self.assertNotIn(
                    f".{module}", line,
                    f"conversation runtime must not import {module}: {line}",
                )
                self.assertFalse(
                    line.startswith(f"from zroq.{module}")
                    or line.startswith(f"import zroq.{module}"),
                    f"conversation runtime must not import {module}: {line}",
                )

    def test_backend_voice_module_has_no_identity_or_session_imports(self):
        source = (REPO_ROOT / "backend" / "app" / "voice" / "transcription.py").read_text(encoding="utf-8")
        lowered = source.lower()
        for token in ("from app.security", "import identity", "session",
                      "principal", "grant", "confirmation", "lease"):
            self.assertNotIn(
                token, lowered,
                f"voice transcription module must not reference '{token}'",
            )

    def test_frontend_voice_layer_never_calls_confirmation_or_action_surfaces(self):
        """The 3F voice hooks/machine contain no fetch to any confirmation,
        grant, or action endpoint — voice is transport only."""
        for rel in ("frontend/hooks/useVoice.ts",
                    "frontend/hooks/useSpeechOutput.ts",
                    "frontend/lib/voiceMachine.ts"):
            source = (REPO_ROOT / rel).read_text(encoding="utf-8").lower()
            for token in ("confirmation", "/api/actions", "/api/grants",
                          "lease", "authorize", "fetch("):
                self.assertNotIn(
                    token, source,
                    f"{rel} must not contain '{token}' — voice carries no authority",
                )


class SpeechStopSemanticsTests(unittest.TestCase):
    """STOP(target=SPEECH) is explicit and never escalates (spec §10 B6)."""

    def _command(self, target: InteractionTarget) -> InteractionControlCommand:
        return InteractionControlCommand(
            command_id="cmd-3f-1",
            owner_id="owner-3f",
            conversation_id="conv-3f-1",
            created_at=utc_now(),
            command_type=InteractionCommandType.STOP,
            target=target,
        )

    def test_generic_stop_without_target_fails_closed(self):
        with self.assertRaises(ContractValidationError):
            self._command(InteractionTarget.UNKNOWN)

    def test_speech_stop_is_not_action_cancellation(self):
        cmd = self._command(InteractionTarget.SPEECH)
        self.assertTrue(cmd.is_speech_stop)
        self.assertFalse(cmd.requires_action_plane_cancellation)

    def test_action_stop_is_a_distinct_explicit_channel(self):
        cmd = self._command(InteractionTarget.ACTION)
        self.assertFalse(cmd.is_speech_stop)
        self.assertTrue(cmd.requires_action_plane_cancellation)


class TranscriptConvergenceTests(unittest.TestCase):
    """A submitted transcript is byte-identical input to typed text: the
    runtime has no modality parameter, and classification depends only on
    the text (spec §6 C1/C2, §8 TR2, §14 M2/M3)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        db = Path(self._tmp.name) / "phase3f.sqlite3"
        store = PersonalContinuityStore(db)
        owner = "owner-3f"
        adapter = DocumentedMemoryOSAdapter(
            store,
            MemoryCapturePolicyEngine.default_retain(owner),
            memoryos_available=True,
            real_memoryos_verified=False,
        )
        engine = PersonalContinuityEngine(store, adapter)
        self.runtime = ConversationRuntime(engine, DeterministicConversationProvider())
        self.store = store
        self.context = MemoryAccessContext(
            owner_id=owner,
            principal_id=f"principal-{owner}",
            authenticated_owner_id=owner,
            purpose="phase 3f transcript convergence test",
        )

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        self._tmp.cleanup()

    def test_runtime_entrypoint_has_no_modality_parameter(self):
        params = inspect.signature(ConversationRuntime.receive_user_message).parameters
        for forbidden in ("modality", "source", "voice", "interaction_mode"):
            self.assertNotIn(
                forbidden, params,
                "the runtime must not know or care whether text was spoken",
            )

    def test_spoken_yes_is_a_plain_message_not_a_control_or_confirmation(self):
        self.runtime.start_conversation(self.context, conversation_id="conv-3f-yes")
        result = self.runtime.receive_user_message(
            self.context, "conv-3f-yes", "yes", auto_stream=True)
        # "yes" is neither a runtime control command nor any form of
        # confirmation — it is an ordinary message that got a normal reply.
        self.assertEqual(result.command, RuntimeCommandKind.NONE)
        self.assertTrue(result.response_id)

    def test_memory_commands_classify_identically_regardless_of_origin(self):
        """The same strings a user could speak classify exactly as typed —
        there is one classification path with one input: the text."""
        self.runtime.start_conversation(self.context, conversation_id="conv-3f-mem")
        remember = self.runtime.receive_user_message(
            self.context, "conv-3f-mem", "remember this: I prefer green tea")
        self.assertEqual(remember.command, RuntimeCommandKind.REMEMBER_THIS)

        dont = self.runtime.receive_user_message(
            self.context, "conv-3f-mem", "don't remember this: my temporary code")
        self.assertEqual(dont.command, RuntimeCommandKind.DO_NOT_REMEMBER)

        show = self.runtime.receive_user_message(
            self.context, "conv-3f-mem", "what do you remember about tea")
        self.assertEqual(show.command, RuntimeCommandKind.SHOW_MEMORY)


if __name__ == "__main__":
    unittest.main()
