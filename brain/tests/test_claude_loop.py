"""La boucle d'outils de ClaudeLLM, avec un faux client Anthropic (aucun appel réseau)."""

import asyncio
import dataclasses
from types import SimpleNamespace

from jarvis_brain.config import Settings
from jarvis_brain.llm.claude import ClaudeLLM

USAGE = SimpleNamespace(input_tokens=10, cache_read_input_tokens=0, output_tokens=5)


def text(t):
    return SimpleNamespace(type="text", text=t)


def tool_use(id_, name, input_):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


class FakeStream:
    def __init__(self, chunks, final):
        self.chunks, self.final = chunks, final

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    def text_stream(self):
        async def gen():
            for c in self.chunks:
                yield c
        return gen()

    async def get_final_message(self):
        return self.final


class FakeClient:
    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **params):
        # Copie : la liste de messages continue d'être modifiée après l'appel.
        self.calls.append({**params, "messages": list(params["messages"])})
        chunks, content, stop = self.rounds.pop(0)
        return FakeStream(chunks, SimpleNamespace(content=content, stop_reason=stop, usage=USAGE))


class FakeToolBox:
    def __init__(self):
        self.ran = []

    def definitions(self):
        return [{"name": "check_emails", "input_schema": {"type": "object"}}]

    async def run(self, tool_use_id, name, raw_input):
        self.ran.append((name, raw_input))
        return {"type": "tool_result", "tool_use_id": tool_use_id, "content": '{"count": 2}'}


async def collect(llm, toolbox):
    return "".join([t async for t in llm.stream([{"type": "text", "text": "sys"}],
                                                 [{"role": "user", "content": "Mes courriels ?"}], toolbox)])


def test_tool_loop_runs_tools_and_continues():
    round1 = ([], [SimpleNamespace(type="thinking", thinking="", signature="sig"),
                   tool_use("tu_1", "check_emails", {"days": 1})], "tool_use")
    round2 = (["Vous avez ", "deux courriels."], [text("Vous avez deux courriels.")], "end_turn")
    client = FakeClient([round1, round2])
    toolbox = FakeToolBox()
    llm = ClaudeLLM(dataclasses.replace(Settings()), client=client)

    out = asyncio.run(collect(llm, toolbox))

    assert out == "Vous avez deux courriels."
    assert toolbox.ran == [("check_emails", {"days": 1})]
    second = client.calls[1]["messages"]
    # Le tour de l'assistant est renvoyé intact (réflexion comprise), suivi des résultats.
    assert second[1]["role"] == "assistant" and second[1]["content"][0].type == "thinking"
    assert second[2]["content"][0]["tool_use_id"] == "tu_1"
    assert client.calls[0]["tools"] and client.calls[0]["fallbacks"] == "default"
    # Système et outils identiques d'un appel à l'autre (cache et réflexion préservés).
    assert client.calls[0]["system"] == client.calls[1]["system"]
    assert client.calls[0]["tools"] == client.calls[1]["tools"]


def test_text_before_and_after_tool_is_separated():
    round1 = (["Je regarde."], [text("Je regarde."), tool_use("tu_1", "check_emails", {})], "tool_use")
    round2 = (["Rien de neuf."], [text("Rien de neuf.")], "end_turn")
    llm = ClaudeLLM(Settings(), client=FakeClient([round1, round2]))
    assert asyncio.run(collect(llm, FakeToolBox())) == "Je regarde. Rien de neuf."


def test_truncated_tool_call_is_not_executed():
    round1 = ([], [tool_use("tu_1", "check_emails", {"da": 1})], "max_tokens")
    toolbox = FakeToolBox()
    llm = ClaudeLLM(Settings(), client=FakeClient([round1]))
    out = asyncio.run(collect(llm, toolbox))
    assert toolbox.ran == [] and "coupée" in out


def test_refusal_is_spoken_politely():
    llm = ClaudeLLM(Settings(), client=FakeClient([([], [], "refusal")]))
    assert "pas pouvoir répondre" in asyncio.run(collect(llm, FakeToolBox()))
