from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Work:
    operation_id: str
    conversation_id: str
    kind: str
    attempt: int = 1
    source_operation_id: str | None = None

    def retried(self):
        return replace(self, attempt=self.attempt + 1)
