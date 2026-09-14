"""Real LangChain StructuredTools bound to the MemoryService.

These are the tools an LLM can call in REAL AI mode. In DEMO mode the same
functions are invoked directly by the deterministic planner, so both paths hit
exactly one memory implementation.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ..memory.service import MemoryService


class SearchMemoryArgs(BaseModel):
    query: str = Field(description="Natural-language query describing what to recall.")
    top_k: int = Field(default=5, ge=1, le=20, description="Maximum memories to return.")


class SaveMemoryArgs(BaseModel):
    content: str = Field(description="The durable fact or preference to remember, in third person.")
    category: str = Field(default="CONTEXT",
                          description="One of IDENTITY, PREFERENCE, PROJECT, GOAL, HABIT, "
                                      "CONTEXT, RELATIONSHIP, FACT, COMMUNICATION_STYLE.")
    importance: float = Field(default=0.7, ge=0.0, le=1.0)


class UpdateMemoryArgs(BaseModel):
    memory_id: str = Field(description="Existing memory id to update.")
    content: str = Field(description="The corrected or superseding content.")
    reason: str = Field(default="Updated by agent")


class DeleteMemoryArgs(BaseModel):
    memory_id: str = Field(description="Existing memory id to delete.")


class ConsolidateMemoryArgs(BaseModel):
    memory_ids: list[str] = Field(description="Two or more related memory ids to merge.")
    content: str | None = Field(default=None, description="Optional merged statement.")


def build_memory_tools(service: MemoryService, user_id: str,
                       thread_id: str | None = None,
                       on_event: Callable[[str, dict[str, Any]], None] | None = None
                       ) -> list[StructuredTool]:
    """Create user-scoped LangChain tools. user_id is bound server-side so a
    model can never read another namespace."""

    def emit(kind: str, payload: dict[str, Any]) -> None:
        if on_event:
            on_event(kind, payload)

    def search_memory(query: str, top_k: int = 5) -> str:
        results = service.search(user_id, query, top_k=top_k)
        emit("SEARCH_MEMORY", {"query": query, "count": len(results)})
        if not results:
            return "NO_STRONG_MATCH: no stored memory is relevant to that query."
        return json.dumps([
            {"memory_id": r.memory.id, "content": r.memory.content,
             "category": r.memory.category, "relevance": round(r.score, 3),
             "why": r.reasons, "strength": r.strength}
            for r in results], indent=2)

    def save_memory(content: str, category: str = "CONTEXT", importance: float = 0.7) -> str:
        result = service.create(user_id, content, category=category,
                                importance=importance, source="agent-tool",
                                thread_id=thread_id)
        emit("SAVE_MEMORY", {"action": result["action"],
                             "memory_id": result["memory"]["id"]})
        return json.dumps({"action": result["action"], "memory_id": result["memory"]["id"],
                           "content": result["memory"]["content"],
                           "category": result["memory"]["category"],
                           "version": result["memory"]["version"]})

    def update_memory(memory_id: str, content: str, reason: str = "Updated by agent") -> str:
        try:
            mem = service.update(memory_id, content=content, reason=reason)
        except KeyError:
            return f"ERROR: memory {memory_id} does not exist."
        emit("UPDATE_MEMORY", {"memory_id": memory_id, "version": mem.version})
        return json.dumps({"memory_id": mem.id, "content": mem.content, "version": mem.version})

    def delete_memory(memory_id: str) -> str:
        ok = service.delete(memory_id)
        emit("DELETE_MEMORY", {"memory_id": memory_id, "deleted": ok})
        return json.dumps({"memory_id": memory_id, "deleted": ok})

    def consolidate_memory(memory_ids: list[str], content: str | None = None) -> str:
        try:
            result = service.consolidate(user_id, memory_ids, content)
        except ValueError as exc:
            return f"ERROR: {exc}"
        emit("CONSOLIDATE_MEMORY", {"memory_id": result["memory"]["id"]})
        return json.dumps({"memory_id": result["memory"]["id"],
                           "content": result["memory"]["content"],
                           "source_memory_ids": result["source_memory_ids"]})

    return [
        StructuredTool.from_function(
            func=search_memory, name="search_memory",
            description="Search the user's long-term memory for relevant stored facts, "
                        "preferences, projects or goals. Use this before answering any "
                        "question about the user.",
            args_schema=SearchMemoryArgs),
        StructuredTool.from_function(
            func=save_memory, name="save_memory",
            description="Store a NEW durable fact about the user (identity, preference, "
                        "project, goal, habit). Do not store transient questions.",
            args_schema=SaveMemoryArgs),
        StructuredTool.from_function(
            func=update_memory, name="update_memory",
            description="Update an existing memory when the user provides newer, "
                        "contradicting information. Creates a new version.",
            args_schema=UpdateMemoryArgs),
        StructuredTool.from_function(
            func=delete_memory, name="delete_memory",
            description="Delete a memory the user asks to forget.",
            args_schema=DeleteMemoryArgs),
        StructuredTool.from_function(
            func=consolidate_memory, name="consolidate_memory",
            description="Merge two or more closely related memories into one richer memory.",
            args_schema=ConsolidateMemoryArgs),
    ]
