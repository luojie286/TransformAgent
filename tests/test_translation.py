from time import sleep

import pytest
from PySide6.QtCore import QEventLoop, QTimer

from conftest import sse_delta
from transformagent.translation import SseReader, StreamError, TranslationClient, TranslationConfig, request_payload


def run_request(client, endpoint, source="This pull request fixes a race condition in React."):
    loop = QEventLoop()
    fragments, outcomes = [], []
    request = client.start(TranslationConfig(endpoint, "qwen-mt-flash", "synthetic-key"), source)
    request.delta.connect(fragments.append)
    request.succeeded.connect(lambda: (outcomes.append("success"), loop.quit()))
    request.failed.connect(lambda message: (outcomes.append(message), loop.quit()))
    safety_timer = QTimer(loop, singleShot=True)
    safety_timer.timeout.connect(loop.quit)
    safety_timer.start(2500)
    loop.exec()
    assert len(outcomes) == 1, outcomes
    return fragments, outcomes[0]


def test_fragmented_utf8_and_ignorable_sse_events():
    reader = SseReader()
    first_event = sse_delta("中").replace(b"\n", b"\r\n")
    split = first_event.index("中".encode()) + 1
    assert reader.feed(b": keepalive\n\n" + first_event[:split]) == []
    assert reader.feed(first_event[split:]) == ["中"]
    assert reader.feed(b'data: {"choices":[],"usage":{"total_tokens":12}}\n\n') == []
    assert reader.feed(sse_delta("文") + b"data: [DONE]\r\n\r\n") == ["文"]
    assert reader.complete
    assert reader.finish() == []


def test_truncated_and_invalid_streams():
    reader = SseReader()
    assert reader.feed(sse_delta("部分译文")) == ["部分译文"]
    with pytest.raises(StreamError, match="中断"):
        reader.finish()
    with pytest.raises(StreamError, match="无法解析"):
        SseReader().feed(b'data: {"error":{"message":"private details"}}\n\n')
    with pytest.raises(StreamError, match="无法解析"):
        SseReader().feed(b"data: {invalid}\n\n")


def test_glossary_retains_english_terms_inside_japanese_text():
    payload = request_payload("qwen-mt-flash", "このpull requestでReactorのrace conditionを修正。")
    terms = payload["translation_options"]["terms"]
    assert {"source": "pull request", "target": "pull request"} in terms
    assert {"source": "race condition", "target": "race condition"} in terms
    assert {"source": "React", "target": "React"} not in terms


def test_glossary_covers_ai_terms_prefers_longer_matches_and_dedupes_case():
    payload = request_payload(
        "qwen-mt-flash",
        "Fine-tuning the LLM with LoRA improved the React Native render pipeline.",
    )
    terms = payload["translation_options"]["terms"]
    assert {"source": "Fine-tuning", "target": "Fine-tuning"} in terms
    assert {"source": "LLM", "target": "LLM"} in terms
    assert {"source": "LoRA", "target": "LoRA"} in terms
    assert {"source": "React Native", "target": "React Native"} in terms
    assert {"source": "React", "target": "React"} not in terms
    deduped = request_payload("qwen-mt-flash", "llm vs LLM.")["translation_options"]["terms"]
    assert deduped == [{"source": "llm", "target": "llm"}]
    plural = request_payload("qwen-mt-flash", "Many pull requests hit the stack traces.")["translation_options"]["terms"]
    assert {"source": "pull requests", "target": "pull requests"} in plural
    assert {"source": "stack traces", "target": "stack traces"} in plural


def test_http_stream_and_qwen_translation_options(app, http_endpoint):
    def response(handler):
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.end_headers()
        for fragment in ("这个 pull request ", "修复了 race condition。"):
            handler.wfile.write(sse_delta(fragment))
            handler.wfile.flush()
            sleep(0.02)
        handler.wfile.write(b"data: [DONE]\n\n")

    endpoint, calls = http_endpoint(response)
    fragments, outcome = run_request(TranslationClient(), endpoint)
    assert outcome == "success"
    assert fragments == ["这个 pull request ", "修复了 race condition。"]
    [(path, auth, payload)] = calls
    assert path == "/v1/chat/completions"
    assert auth == "Bearer synthetic-key"
    assert payload["stream"] is True
    assert payload["messages"] == [
        {"role": "user", "content": "This pull request fixes a race condition in React."},
    ]
    assert payload["translation_options"]["target_lang"] == "Chinese"
    terms = payload["translation_options"]["terms"]
    assert {"source": "race condition", "target": "race condition"} in terms


@pytest.mark.parametrize(("status", "message"), ((401, "API Key"), (429, "限流"), (500, "HTTP 500")))
def test_http_errors_are_actionable(app, http_endpoint, status, message):
    def response(handler):
        handler.send_error(status)

    endpoint, _ = http_endpoint(response)
    fragments, outcome = run_request(TranslationClient(), endpoint)
    assert not fragments
    assert message in outcome


def test_timeout_and_incomplete_http_stream(app, http_endpoint):
    def slow(handler):
        sleep(0.25)
        handler.send_response(200)
        handler.end_headers()

    def incomplete(handler):
        handler.send_response(200)
        handler.end_headers()
        handler.wfile.write(sse_delta("仍保留已接收的译文。"))

    slow_url, _ = http_endpoint(slow)
    assert "超时" in run_request(TranslationClient(deadline_ms=80), slow_url)[1]
    incomplete_url, _ = http_endpoint(incomplete)
    fragments, outcome = run_request(TranslationClient(), incomplete_url)
    assert fragments == ["仍保留已接收的译文。"]
    assert "中断" in outcome


def test_redirect_does_not_forward_key_or_source(app, http_endpoint):
    target, unexpected = http_endpoint(lambda handler: handler.send_error(404))

    def redirect(handler):
        handler.send_response(307)
        handler.send_header("Location", target)
        handler.end_headers()

    endpoint, calls = http_endpoint(redirect)
    assert "HTTP 307" in run_request(TranslationClient(), endpoint)[1]
    assert len(calls) == 1
    assert unexpected == []
