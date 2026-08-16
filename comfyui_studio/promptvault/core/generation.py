"""Доменные модели одной генерации: сама генерация, её изображения и LoRA."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class ImageData:
    """Одно изображение, полученное в результате генерации."""

    file: str
    seed: int | None = None

    @property
    def filename(self) -> str:
        return self.file


@dataclass(slots=True)
class LoraData:
    """Одна LoRA, использованная при генерации."""

    filename: str
    strength: float
    source: str | None = None


@dataclass(slots=True)
class Generation:
    """Одна генерация: конфигурация (модель, сэмплер, промпты, LoRA) и
    список полученных изображений, плюс пользовательские избранное/рейтинг.
    """

    path: Path

    timestamp: str
    generation_time: float

    model: str
    cfg: float
    steps: int
    sampler: str

    positive: str
    negative: str

    images: list[ImageData] = field(default_factory=list)
    loras: list[LoraData] = field(default_factory=list)

    extra_data: dict = field(default_factory=dict)

    favorite: bool = False
    rating: int = 0

    # пользовательские теги (задача: пользовательские теги) — хранятся
    # отдельно от исходного JSON (см. app/core/repository.set_custom_tags),
    # переживают редактирование метаданных и ре-синхронизацию с диском
    custom_tags: list[str] = field(default_factory=list)

    # кэшированный семантический эмбеддинг промпта (только positive,
    # см. app/core/repository._embedding_text) — bytes (float32) или
    # None, если ещё не посчитан (либо библиотека эмбеддингов недоступна)
    embedding: bytes | None = None

    # оценка релевантности при семантическом поиске (0..1, см.
    # GenerationFilter) — не персистентна, пересчитывается на лету при
    # каждом apply(); вне контекста активного semantic_query не имеет
    # смысла и остаётся 0.0
    semantic_score: float = 0.0

    # первичный ключ в БД; None для генераций, ещё не сохранённых
    id: int | None = None

    @property
    def directory(self) -> Path:
        return self.path.parent

    @property
    def image_count(self) -> int:
        return len(self.images)

    @property
    def title(self) -> str:
        return f"{self.timestamp} | {self.model} | {self.image_count} imgs"

    def image_path(self, index: int) -> Path:
        return self.directory / self.images[index].file
