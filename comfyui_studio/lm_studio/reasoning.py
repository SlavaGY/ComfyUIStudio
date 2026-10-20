"""Separate an optional leading <think> block, including split stream tokens."""


class ReasoningParser:
    def __init__(self):
        self.pending = ""
        self.phase = "start"

    def feed(self, text, *, final=False):
        self.pending += text
        if self.phase == "start":
            stripped = self.pending.lstrip()
            if not final and "<think>".startswith(stripped):
                return "", ""
            if stripped.startswith("<think>"):
                self.pending = stripped[len("<think>"):]
                self.phase = "reasoning"
            else:
                self.phase = "content"
        if self.phase == "content":
            content, self.pending = self.pending, ""
            return content, ""
        closing = "</think>"
        if closing in self.pending:
            reasoning, content = self.pending.split(closing, 1)
            self.pending = ""
            self.phase = "content"
            return content, reasoning
        # Retain only a possible partial closing tag until the next chunk.
        keep = 0
        if not final:
            for size in range(1, min(len(closing), len(self.pending) + 1)):
                if self.pending.endswith(closing[:size]):
                    keep = size
        end = len(self.pending) - keep
        reasoning, self.pending = self.pending[:end], self.pending[end:]
        return "", reasoning
