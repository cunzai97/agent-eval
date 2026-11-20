from dataclasses import dataclass


@dataclass(eq=False)
class Checkpoint:
    position: int
    identity_tokens: tuple[int, ...]
    is_root: bool = False

    @property
    def fingerprint(self):
        return sum(self.identity_tokens) % 13
