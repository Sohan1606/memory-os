"""LangChain StructuredTools must perform real memory operations."""
import json

from langchain_core.tools import StructuredTool

from app.agent.tools import build_memory_tools


def tools_for(runtime, user):
    return {t.name: t for t in build_memory_tools(runtime.memory, user, "tool-thread")}


def test_tools_are_langchain_structured_tools(runtime, user):
    tools = build_memory_tools(runtime.memory, user, "t")
    assert len(tools) == 5
    assert all(isinstance(t, StructuredTool) for t in tools)
    assert {t.name for t in tools} == {"search_memory", "save_memory", "update_memory",
                                       "delete_memory", "consolidate_memory"}


def test_save_then_search_then_delete(runtime, user):
    tools = tools_for(runtime, user)
    saved = json.loads(tools["save_memory"].invoke(
        {"content": "Plays classical guitar every Sunday.", "category": "HABIT"}))
    mid = saved["memory_id"]
    assert runtime.memory.get(mid) is not None

    found = tools["search_memory"].invoke({"query": "classical guitar", "top_k": 5})
    assert mid in found

    deleted = json.loads(tools["delete_memory"].invoke({"memory_id": mid}))
    assert deleted["deleted"] is True
    assert runtime.memory.get(mid) is None


def test_update_tool_increments_version(runtime, user):
    tools = tools_for(runtime, user)
    mid = json.loads(tools["save_memory"].invoke(
        {"content": "Drinks black coffee in the morning.", "category": "HABIT"}))["memory_id"]
    updated = json.loads(tools["update_memory"].invoke(
        {"memory_id": mid, "content": "Drinks green tea in the morning.",
         "reason": "User corrected"}))
    assert updated["version"] == 2
    assert runtime.memory.get(mid).content == "Drinks green tea in the morning."
    runtime.memory.delete(mid)


def test_search_reports_no_match_honestly(runtime, user):
    tools = tools_for(runtime, user)
    out = tools["search_memory"].invoke({"query": "xyzzy quantum barnacle", "top_k": 5})
    assert "NO_STRONG_MATCH" in out


def test_tools_are_user_scoped(runtime):
    """A tool bound to user B must never read user A's memories."""
    runtime.memory.create("user-A", "User A has a private passphrase habit.",
                          category="HABIT", allow_duplicate=True)
    tools_b = {t.name: t for t in build_memory_tools(runtime.memory, "user-B", "t")}
    out = tools_b["search_memory"].invoke({"query": "private passphrase", "top_k": 5})
    assert "passphrase" not in out
