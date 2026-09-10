"""Streaming 取消（§3.2）。直接驱动 Gateway.stream_events，确定性验证取消时序。"""

from __future__ import annotations

import asyncio

from mini_llm_gateway.adapters.fake import FakeAdapter
from mini_llm_gateway.app import create_app
from mini_llm_gateway.governance.tracing import InMemoryTraceSink
from mini_llm_gateway.protocol.messages import Message
from mini_llm_gateway.protocol.request import LLMRequest
from tests.conftest import make_config, make_prompt_store


async def test_explicit_cancel_stops_pulling_and_records_cancelled() -> None:
    sink = InMemoryTraceSink()
    fake = FakeAdapter(chunks=["a", "b", "c", "d"])
    app = create_app(
        make_config(),
        adapter_override=fake,
        prompt_store=make_prompt_store(),
        trace_sink=sink,
    )
    gateway = app.state.gateway
    request = LLMRequest(model="general-primary", messages=[Message(role="user", content="hi")])
    messages = gateway.prepare_stream(request)
    cancel_event = asyncio.Event()

    frames: list[str] = []
    async for frame in gateway.stream_events(
        request, messages, request_id="req_test", cancel_event=cancel_event
    ):
        frames.append(frame)
        if len(frames) == 2:
            cancel_event.set()

    # 取消后停止拉取上游：不再产出后续分块。
    assert len(frames) == 2
    assert fake.chunks_emitted <= 2
    # 未重复已产出内容，且产生一条 cancelled 审计记录。
    assert all('"type": "content.delta"' in frame for frame in frames)
    trace = sink.list()[-1]
    assert trace.status == "cancelled"
    assert trace.request_id == "req_test"
