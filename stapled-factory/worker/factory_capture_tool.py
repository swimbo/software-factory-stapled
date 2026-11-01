"""Pinned SDK extension: bounded output artifacts, no extra agent loop or egress."""
import json
from pathlib import Path
from typing import ClassVar, Literal
import uuid

from pydantic import Field, model_validator
from rich.text import Text
from openhands.sdk.tool import Action, Observation, ToolDefinition, ToolExecutor, register_tool
from output_artifacts import CaptureStore


class CaptureAction(Action):
    operation: Literal['capture', 'read'] = Field(description='Capture a command or read a retained output page.')
    argv: list[str] | None = Field(default=None, description='Executable and arguments. No shell expansion unless explicitly invoking a shell.')
    artifact_id: str | None = Field(default=None, description='Opaque ID from this conversation only.')
    offset: int = Field(default=0, ge=0, description='UTF-8 byte offset returned by the previous page.')
    timeout: float = Field(default=30, gt=0, le=60, description='Capture wall-clock seconds, at most 60.')

    @model_validator(mode='after')
    def operation_fields(self):
        if self.operation == 'capture':
            if not self.argv or self.artifact_id is not None or self.offset:
                raise ValueError('capture requires argv only')
        elif self.argv is not None or not self.artifact_id:
            raise ValueError('read requires artifact_id only')
        return self

    @property
    def visualize(self):
        return Text('Factory output ' + self.operation)


class CaptureObservation(Observation):
    @property
    def visualize(self):
        return Text(self.text)


class CaptureExecutor(ToolExecutor):
    def __init__(self, root, workspace):
        self.root, self.workspace = root, workspace

    def __call__(self, action, conversation=None):
        # Use the SDK's registered-secret masking as well as the core patterns.
        registry = getattr(getattr(conversation, 'state', None), 'secret_registry', None)
        def redact(text, *, incomplete):
            if registry is None:
                return text
            masked = registry.mask_secrets_in_output(text)
            stream = registry.compile_stream_mask()
            # On incomplete capture, withhold a suffix that could be part of a
            # registered secret cut by the byte limit or cancellation.
            safe = stream.feed(masked)
            return safe if incomplete else safe + stream.flush()
        # Snapshot the per-run token: the conversation clears it after the run.
        token = getattr(conversation, 'cancel_token', None)
        cancelled = (lambda: token.is_cancelled) if token is not None else None
        try:
            store = CaptureStore(self.root, self.workspace, redact=redact)
            result = (store.capture(action.argv, timeout=action.timeout, cancelled=cancelled) if action.operation == 'capture'
                      else store.read(action.artifact_id, offset=action.offset))
            return CaptureObservation.from_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')))
        except (OSError, ValueError, RuntimeError):
            # Never put exception text, host paths or command arguments in errors.
            return CaptureObservation.from_text('Output artifact unavailable; command effects may require reconciliation.', is_error=True)


class FactoryCaptureTool(ToolDefinition[CaptureAction, CaptureObservation]):
    name: ClassVar[str] = 'factory_capture'
    catalog_description: ClassVar[str] = 'Capture bounded command output and read private artifact pages.'

    @classmethod
    def create(cls, conv_state, **params):
        if params:
            raise ValueError('factory_capture has no configurable storage paths or quotas')
        identity = uuid.UUID(str(conv_state.id))
        root = Path('/tmp') / ('factory-output-' + str(identity))
        return [cls(description=(
            'Run a command in the conversation workspace and retain redacted merged stdout/stderr. '
            'Returns at most 4096 UTF-8 bytes of untrusted output plus an opaque ID and SHA256. '
            'Use read with next_offset for more pages. Separate process: terminal shell state is not shared. '
            'Limits: 60 seconds, 1 MiB capture, 8 MiB store, 24-hour retention. '
            'Cancellation/timeout/output limits terminate the process group; capture_complete=false means incomplete output. '
            'Commands have ordinary worker permissions and may mutate the workspace; never blindly replay them.'),
            action_type=CaptureAction, observation_type=CaptureObservation,
            executor=CaptureExecutor(root, conv_state.workspace.working_dir))]


register_tool(FactoryCaptureTool.name, FactoryCaptureTool)
