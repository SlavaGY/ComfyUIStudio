"""User-selected remote MCP connections; credentials never enter chat history."""

from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator


class Integration(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    url: str = Field(min_length=1, max_length=2048)
    enabled: bool = True
    headers: dict[str, str] = Field(default_factory=dict, max_length=20)
    allowed_tools: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("url")
    @classmethod
    def valid_url(cls, value):
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError("Укажите HTTP/HTTPS URL MCP-сервера без пароля в адресе")
        return value


class IntegrationSettings(BaseModel):
    integrations: list[Integration] = Field(default_factory=list, max_length=20)

    @field_validator("integrations")
    @classmethod
    def unique_names(cls, value):
        if len({item.name for item in value}) != len(value):
            raise ValueError("Имена интеграций должны быть уникальными")
        return value


class IntegrationStore:
    def __init__(self, path):
        self.path = path

    def read(self):
        if not self.path.exists():
            return IntegrationSettings()
        return IntegrationSettings.model_validate_json(self.path.read_text(encoding="utf-8"))

    def save(self, settings):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def tools(self):
        result = []
        for item in self.read().integrations:
            if not item.enabled:
                continue
            tool = {"type": "mcp", "server_label": item.name, "server_url": item.url,
                    "require_approval": "never"}
            if item.headers:
                tool["headers"] = item.headers
            if item.allowed_tools:
                tool["allowed_tools"] = item.allowed_tools
            result.append(tool)
        return result
