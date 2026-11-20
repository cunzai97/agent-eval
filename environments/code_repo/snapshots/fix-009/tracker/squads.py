"""Squad membership."""
from audit.log import log_activity

ENTITY = "squad"


def _require(ws, squad_id):
    squad = ws.squads.get(squad_id)
    if squad is None:
        raise KeyError(f"no squad {squad_id} in {ws.name}")
    return squad


def create_squad(ws, name, *, actor):
    squad_id = ws.next_id("SQUAD-")
    squad = {"id": squad_id, "name": name, "members": [], "archived": False}
    ws.squads[squad_id] = squad
    log_activity(ws.name, ENTITY, squad_id, "create", {"name": name}, actor=actor)
    return dict(squad)


def add_member(ws, squad_id, user_id, role):
    squad = _require(ws, squad_id)
    squad["members"].append(user_id)
    squad.setdefault("roles", {})[user_id] = role
    log_activity(ws.name, ENTITY, squad_id, "add_member", {"member": user_id, "role": role})
    return dict(squad)


def remove_member(ws, squad_id, user_id):
    squad = _require(ws, squad_id)
    if user_id not in squad["members"]:
        raise KeyError(f"{user_id} is not in {squad_id}")
    squad["members"].remove(user_id)
    squad.get("roles", {}).pop(user_id, None)
    log_activity(ws.name, ENTITY, squad_id, "remove_member", {"member": user_id})
    return dict(squad)


def archive_squad(ws, squad_id, *, actor):
    squad = _require(ws, squad_id)
    squad["archived"] = True
    log_activity(ws.name, ENTITY, squad_id, "archive", {"members": len(squad["members"])}, actor=actor)
    return dict(squad)
