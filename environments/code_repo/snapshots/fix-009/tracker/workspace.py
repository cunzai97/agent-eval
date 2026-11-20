"""Workspace handle shared by every tracker module."""


class Workspace:
    """One team workspace; every tracked mutation is scoped to exactly one."""

    def __init__(self, name):
        self.name = name
        self.issues = {}
        self.agents = {}
        self.squads = {}
        self.runs = {}
        self._seq = 0

    def next_id(self, prefix):
        self._seq += 1
        return f"{prefix}{self._seq:03d}"
