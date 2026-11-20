"""Storage layer: one JSON document per kind below the store root."""
import json
import os

from .registry import COLLECTION_ORDER, KINDS

# kind -> file name inside data/. The names are deliberately irregular.
COLLECTION_FILES = {
    "issue": "issues.json",
    "agent": "agents.json",
    "squad": "squads.json",
}


class JsonStore:
    def __init__(self, root):
        self.root = str(root)
        self.data_dir = os.path.join(self.root, "data")
        os.makedirs(self.data_dir, exist_ok=True)
        self._ensure_collections()

    # ---------------- plumbing ----------------

    def _ensure_collections(self):
        for kind in COLLECTION_ORDER:
            path = self._path(kind)
            if not os.path.isfile(path):
                self._write(path, {"seq": 0, "items": []})

    def _path(self, kind):
        return os.path.join(self.data_dir, COLLECTION_FILES[kind])

    def _read(self, path):
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def _write(self, path, document):
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")

    def _doc(self, kind):
        if kind not in KINDS:
            raise ValueError(f"unknown kind: {kind}")
        path = self._path(kind)
        if not os.path.isfile(path):
            return {"seq": 0, "items": []}
        return self._read(path)

    # ---------------- records ----------------

    def create(self, kind, fields):
        document = self._doc(kind)
        document["seq"] = int(document.get("seq", 0)) + 1
        item = {"id": f"{kind}{document['seq']}", "kind": kind}
        item.update(fields)
        document["items"].append(item)
        self._write(self._path(kind), document)
        return dict(item)

    def put(self, kind, item, seq=None):
        """Insert a prepared record verbatim; used by import and repair."""
        document = self._doc(kind)
        document["items"].append(dict(item))
        if seq is not None:
            document["seq"] = max(int(seq), int(document.get("seq", 0)), len(document["items"]))
        self._write(self._path(kind), document)
        return dict(item)

    def get(self, kind, item_id):
        for item in self._doc(kind)["items"]:
            if item["id"] == item_id:
                return dict(item)
        return None

    def update(self, kind, item_id, fields):
        document = self._doc(kind)
        for index, item in enumerate(document["items"]):
            if item["id"] == item_id:
                item.update(fields)
                document["items"][index] = item
                self._write(self._path(kind), document)
                return dict(item)
        raise KeyError(f"no {kind} {item_id}")

    def list(self, kind):
        return [dict(item) for item in self._doc(kind)["items"]]

    def count(self, kind):
        return len(self.list(kind))

    def sequence(self, kind):
        return int(self._doc(kind).get("seq", 0))

    def counts(self):
        return {kind: self.count(kind) for kind in COLLECTION_ORDER}

    def sequences(self):
        return {kind: self.sequence(kind) for kind in COLLECTION_ORDER}
