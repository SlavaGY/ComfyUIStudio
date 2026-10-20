import pytest

from comfyui_studio.lm_studio.reasoning import ReasoningParser


@pytest.mark.parametrize("text,content,reasoning", [
    ("<think>Подумаю…</think>Ответ", "Ответ", "Подумаю…"),
    (" \n<think>thinking</think>\n42", "\n42", "thinking"),
    ("<think>unfinished", "", "unfinished"),
    ("<think>partial</thi", "", "partial</thi"),
    ("Plain <think>quoted</think> text", "Plain <think>quoted</think> text", ""),
    ("<thing>not reasoning", "<thing>not reasoning", ""),
    ("<thi", "<thi", ""),
])
def test_reasoning_tags_split_at_every_possible_stream_boundary(text, content, reasoning):
    for split in range(len(text) + 1):
        parser = ReasoningParser()
        parts = [parser.feed(text[:split]), parser.feed(text[split:]), parser.feed("", final=True)]
        assert "".join(p[0] for p in parts) == content
        assert "".join(p[1] for p in parts) == reasoning
    parser = ReasoningParser()
    parts = [parser.feed(char) for char in text] + [parser.feed("", final=True)]
    assert "".join(p[0] for p in parts) == content
    assert "".join(p[1] for p in parts) == reasoning
