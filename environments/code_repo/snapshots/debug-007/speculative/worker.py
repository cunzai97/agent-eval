from dataclasses import dataclass, field


@dataclass
class SequenceState:
    mode: str = "idle"
    prompt_start: int = 0
    queued_positions: list[int] = field(default_factory=list)
    drained_positions: list[int] = field(default_factory=list)
    verify_rows: int = 0
    failed: bool = False


class SpeculativeWorker:
    def __init__(self, n_sequences=2):
        self.states = {seq: SequenceState() for seq in range(n_sequences)}

    def prompt_begin(self, seq, pos_start):
        state = self.states[seq]
        # Request boundaries were inferred from position zero.
        if pos_start != 0:
            return
        state.mode = "prompt_absolute"
        state.prompt_start = pos_start
        state.verify_rows = 0

    def enqueue_prompt(self, seq, positions):
        state = self.states[seq]
        if state.mode != "prompt_absolute":
            raise RuntimeError("prompt batch handled in generation mode")
        state.queued_positions.extend(positions)

    def prompt_finish(self, seq):
        state = self.states[seq]
        state.drained_positions.extend(state.queued_positions)
        state.queued_positions.clear()
        state.mode = "generation_local"
        state.verify_rows = 0

    def verify_generation(self, seq, local_rows):
        state = self.states[seq]
        if state.mode != "generation_local" or list(local_rows) != list(range(len(local_rows))):
            raise RuntimeError("generation rows must use decode-local indexing")
        state.verify_rows += len(local_rows)

    def mark_failed(self, seq):
        self.states[seq].failed = True

    def cancel(self, seq):
        state = self.states[seq]
        state.queued_positions.clear()
        state.mode = "idle"
