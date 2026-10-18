"""Presenter галереи между UI и GenerationRepository.

GalleryManager объединяет состояние, выбор и пагинацию (gallery_base),
фильтры и загрузку (gallery_filters), файловые операции (gallery_mutations)
и метаданные со статистикой (gallery_metadata). UI подключает сигналы
и обрабатывает события виджетов."""

from __future__ import annotations

from comfyui_studio.promptvault.core.gallery_base import GalleryBase
from comfyui_studio.promptvault.core.gallery_filters import FiltersMixin
from comfyui_studio.promptvault.core.gallery_metadata import MetadataMixin
from comfyui_studio.promptvault.core.gallery_mutations import MutationsMixin


class GalleryManager(FiltersMixin, MutationsMixin, MetadataMixin, GalleryBase):
    """Владеет состоянием галереи и всей логикой вокруг него.

    Сигналы и общий конвейер фильтрации — gallery_base.GalleryBase;
    фильтры/сортировка/загрузка — gallery_filters.FiltersMixin;
    мутации и файловые операции — gallery_mutations.MutationsMixin;
    метаданные и статистика — gallery_metadata.MetadataMixin.
    """
