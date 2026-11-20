"""Agent registration and configuration."""
import hashlib

from audit.log import log_activity

ENTITY = "agent"


def _require(ws, agent_id):
    agent = ws.agents.get(agent_id)
    if agent is None:
        raise KeyError(f"no agent {agent_id} in {ws.name}")
    return agent


def register_agent(ws, name, *, owner, actor):
    agent_id = ws.next_id("AGENT-")
    agent = {"id": agent_id, "name": name, "owner": owner, "model": "qwen", "budget": 0, "paused": False}
    ws.agents[agent_id] = agent
    log_activity(ws.name, ENTITY, agent_id, "register", {"name": name, "owner": owner}, actor=actor)
    return dict(agent)


def rename_agent(ws, agent_id, name, *, actor):
    agent = _require(ws, agent_id)
    agent["name"] = name
    log_activity(ws.name, ENTITY, agent_id, "rename", {"name": name}, actor=actor)
    return dict(agent)


def rotate_key(ws, agent_id):
    agent = _require(ws, agent_id)
    secret = hashlib.sha256(f"{agent_id}:rotated".encode()).hexdigest()[:12]
    agent["key"] = secret
    log_activity(ws.name, ENTITY, agent_id, "rotate_key", {"fingerprint": secret[:6]})
    return dict(agent)


def set_budget(ws, agent_id, budget, *, actor):
    agent = _require(ws, agent_id)
    agent["budget"] = int(budget)
    log_activity(ws.name, ENTITY, agent_id, "set_budget", {"budget": int(budget)}, actor=actor)
    return dict(agent)
