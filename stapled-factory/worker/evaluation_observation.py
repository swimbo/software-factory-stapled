"""Opt-in read-only SDK polling for control profiles in paired evaluation."""
import time
import uuid

PROFILE = 'sdk-poll-v1'


class EvaluationObserver:
    """Observe one original conversation with the stall observer's polling bounds.

    Validation and revision acceptance remain caller-owned. A polling limit or
    error leaves the original attempt unresolved; this adapter never cancels,
    resumes, provisions, sends a diagnostic, or replenishes a reservation.
    """
    def __init__(self, base, conversation_id, *, validate):
        if str(uuid.UUID(conversation_id)) != conversation_id:
            raise ValueError('canonical original conversation required')
        self.base = base
        self.conversation_id = conversation_id
        self.validate = validate

    def _validate(self, conversation_id):
        if conversation_id != self.conversation_id:
            raise ValueError('original conversation required')
        self.validate(conversation_id)

    def _watch(self, conversation_id):
        for _ in range(120):
            self._validate(conversation_id)
            result = self.base.read(conversation_id, None)
            if result is not None:
                return result
            time.sleep(2)
        return None

    def create(self, conversation_id, payload):
        self._validate(conversation_id)
        result = self.base.create(conversation_id, payload)
        return result if result is not None else self._watch(conversation_id)

    def read(self, conversation_id, _payload):
        return self._watch(conversation_id)
