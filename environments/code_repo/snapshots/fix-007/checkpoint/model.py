from dataclasses import dataclass


@dataclass(eq=False)
class Checkpoint:
    n_tokens: int
    identity_tokens: tuple[int, ...]
    full: bool = False
    is_root: bool = False
    owner_id: str = ""

    @property
    def fingerprint(self):
        # Deliberately non-unique: useful only as a bucket hint, never identity.
        return sum(self.identity_tokens) % 17


@dataclass
class Session:
    session_id: str
    tokens: tuple[int, ...]
    load_cost: int


def common_prefix(a, b):
    n = 0
    for left, right in zip(a, b):
        if left != right:
            break
        n += 1
    return n
