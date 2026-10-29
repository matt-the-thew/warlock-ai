"""Claude agent that answers SRD rules questions via a `search_srd` tool.

Each turn runs a tool-use loop: Claude decides what to search, the index
returns `search_result` blocks, and Claude answers with citations that point
back at those results. `Agent.ask()` yields events as they stream, so the CLI
and a future HTTP endpoint can both render them.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal

import anthropic

from .index import Hit, SrdIndex

MODEL = "claude-opus-5-5"
# Rules lookups need careful reading more than long deliberation.
EFFORT = "medium"
MAX_TOKENS = 64000
MAX_TOOL_ROUNDS = 8
SEARCH_K = 5

SYSTEM_PROMPT = """\
You are a rules expert for the Dungeons & Dragons System Reference Document 5.2.1 \
(SRD 5.2.1, the 2024 rules), helping players and Game Masters at the table.

Answer only from text returned by the search_srd tool, never from memory of other \
editions, sourcebooks or house rules. Search before answering any rules question. \
Questions often hinge on several rules at once (a condition plus a spell plus an \
action), so search for each piece separately and follow "See also" references \
when they matter.

If the SRD does not cover something, say so plainly rather than filling the gap; \
you may then note that it is up to the GM. Quote exact numbers (damage dice, \
ranges, DCs) as written. Keep answers short and direct: lead with the ruling, \
then the supporting detail."""

SEARCH_TOOL = {
    "name": "search_srd",
    "description": (
        "Search the D&D SRD 5.2.1 rules text. Returns the most relevant entries "
        "(spells, conditions, glossary terms, class features, feats, equipment, "
        "magic items, monster stat blocks), each with its section and page. "
        "Matches both exact rules terms and paraphrased questions. Use short, "
        "focused queries naming one rule each, e.g. 'Grappled condition' or "
        "'opportunity attack when target teleports'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to look up."},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    "strict": True,
    "eager_input_streaming": True,
}


@dataclass
class Event:
    type: Literal["search", "text", "citation", "done", "error"]
    data: dict[str, Any]


def hit_to_search_result(hit: Hit) -> dict:
    """One index hit as a citable `search_result` block.

    Each paragraph is its own text block: Claude cites whole blocks, so this
    keeps a citation of one line of a long spell from returning all of it.
    """
    return {
        "type": "search_result",
        "source": f"srd-5.2.1#{hit.id}",
        "title": f"{hit.citation} — {' > '.join(hit.path[:-1])}",
        "content": [
            {"type": "text", "text": p} for p in hit.text.split("\n") if p.strip()
        ],
        "citations": {"enabled": True},
    }


class Agent:
    """A conversation with the rules agent. Keeps history across `ask()` calls."""

    def __init__(self, index: SrdIndex, client: anthropic.Anthropic | None = None):
        self.index = index
        self.client = client or anthropic.Anthropic()
        self.messages: list[dict] = []
        # search_result_index in a citation counts every search_result block in
        # the request, across all turns, in order; this list mirrors that.
        self.sources: list[Hit] = []

    def ask(self, question: str) -> Iterator[Event]:
        self.messages.append({"role": "user", "content": question})

        json_retries = 0
        for _ in range(MAX_TOOL_ROUNDS):
            try:
                response = yield from self._stream_turn()
            except ValueError:
                # Tool input JSON the SDK could not parse at all; there is no
                # tool_use_id to answer, so re-issue the turn (bounded).
                json_retries += 1
                if json_retries > 2:
                    raise
                continue
            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "refusal":
                yield Event("error", {"message": "The request was declined."})
                return
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                yield Event("done", {"stop_reason": response.stop_reason})
                return
            if response.stop_reason == "max_tokens":
                yield Event(
                    "error", {"message": "Response hit max_tokens mid tool call."}
                )
                return

            results = []
            for block in tool_uses:
                results.append((yield from self._run_tool(block)))
            self.messages.append({"role": "user", "content": results})

        yield Event(
            "error", {"message": f"Stopped after {MAX_TOOL_ROUNDS} search rounds."}
        )

    def _stream_turn(self) -> Iterator[Event]:
        with self.client.beta.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=[SEARCH_TOOL],
            messages=self.messages,
            output_config={"effort": EFFORT},
            # Cache the system prompt, tool and history prefix across rounds.
            cache_control={"type": "ephemeral"},
            # On a safety-classifier decline, retry on a fallback model.
            fallbacks="default",
            betas=["server-side-fallback-2026-07-01"],
        ) as stream:
            # A cited block's citations stream before its text; hold them until
            # the block ends so each marker follows the sentence it supports.
            pending: list[Event] = []
            for event in stream:
                if event.type == "text":
                    yield Event("text", {"text": event.text})
                elif event.type == "citation":
                    pending.append(Event("citation", self._citation(event.citation)))
                elif event.type == "content_block_stop":
                    yield from pending
                    pending.clear()
            return stream.get_final_message()

    def _run_tool(self, block) -> Iterator[Event]:
        # With eager input streaming the API does not validate tool input, so
        # check it here; a malformed call gets an error result Claude can retry.
        query = block.input.get("query") if isinstance(block.input, dict) else None
        if (
            block.name != "search_srd"
            or not isinstance(query, str)
            or not query.strip()
        ):
            return {
                "type": "tool_result",
                "tool_use_id": block.id,
                "is_error": True,
                "content": f"Invalid call to {block.name}: expected {{'query': str}}, got {block.input!r}",
            }

        yield Event("search", {"query": query})
        hits = self.index.search(query, SEARCH_K)
        if not hits:
            return {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": [{"type": "text", "text": "No results found."}],
            }
        self.sources.extend(hits)
        return {
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": [hit_to_search_result(h) for h in hits],
        }

    def _citation(self, citation) -> dict:
        data = {"cited_text": getattr(citation, "cited_text", "")}
        i = getattr(citation, "search_result_index", None)
        if i is not None and i < len(self.sources):
            hit = self.sources[i]
            data |= {
                "id": hit.id,
                "title": hit.title,
                "path": hit.path,
                "page_start": hit.page_start,
                "page_end": hit.page_end,
                "label": hit.citation,
            }
        return data
