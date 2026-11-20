"""In-memory operation scheduler with explicit attempt tokens."""
from .policy import RetryPolicy


class RetryScheduler:
    def __init__(self, policy=None):
        self.policy = policy or RetryPolicy()
        self._ops = {}

    def submit(self, operation_id, payload, now=0.0):
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("operation_id must be a non-empty string")
        operation_id = operation_id.strip()
        if operation_id in self._ops:
            raise ValueError("operation_id already exists")
        self._ops[operation_id] = {
            "id": operation_id, "payload": payload, "attempts": 0,
            "state": "ready", "due": float(now), "token": 1,
        }
        return 1

    def start(self, now):
        candidates = [op for op in self._ops.values()
                      if op["state"] == "ready" and op["due"] <= now]
        if not candidates:
            return None
        op = sorted(candidates, key=lambda item: item["id"])[0]
        op["state"] = "running"
        return {key: op[key] for key in ("id", "payload", "attempts", "token")}

    def succeed(self, operation_id, token):
        op = self._ops.get(operation_id)
        if not op or op["state"] != "running" or op["token"] != token:
            return False
        op["state"] = "succeeded"
        op["due"] = None
        return True

    def fail(self, operation_id, token, now):
        op = self._ops.get(operation_id)
        if not op or op["state"] != "running" or op["token"] != token:
            return "stale"
        op["attempts"] += 1
        if op["attempts"] >= self.policy.max_attempts:
            op["state"] = "failed"
            op["due"] = None
            return "failed"
        op["state"] = "ready"
        op["due"] = float(now) + self.policy.delay(op["attempts"])
        return "retry"

    def cancel(self, operation_id):
        op = self._ops.get(operation_id)
        if not op or op["state"] != "ready":
            return False
        op["state"] = "cancelled"
        op["due"] = None
        op["token"] += 1
        return True

    def snapshot(self):
        return [
            {key: op[key] for key in ("id", "state", "attempts", "due", "token")}
            for op in sorted(self._ops.values(), key=lambda item: item["id"])
        ]
