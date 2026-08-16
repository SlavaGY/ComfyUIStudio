from __future__ import annotations

import logging
from dataclasses import dataclass

from comfyui_studio.promptvault.config import SEMANTIC_SIMILARITY_THRESHOLD
from comfyui_studio.promptvault.core import embedding
from comfyui_studio.promptvault.core.generation import Generation

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class FilterOptions:

    search: str = ""

    # поиск по смыслу промпта (векторный, а не по подстроке) — см.
    # app/core/embedding.py; работает независимо от search и
    # комбинируется с ним и остальными условиями через И
    semantic_query: str = ""

    model: str | None = None
    sampler: str | None = None

    min_cfg: float | None = None
    max_cfg: float | None = None

    min_steps: int | None = None
    max_steps: int | None = None

    loras: list[str] | None = None

    # LoRA-исключения (задача: трёхстанное состояние фильтра
    # LoRA/тегов — включить/исключить/нейтрально): None — не
    # фильтровать; иначе генерация не должна содержать НИ ОДНОЙ из
    # перечисленных LoRA. Один и тот же элемент не может одновременно
    # быть и в loras, и в excluded_loras — это гарантирует UI
    # (FilterPopup), а не эта модель данных
    excluded_loras: list[str] | None = None

    # None — не фильтровать; True — только избранные; False — без избранных
    favorites_only: bool | None = None

    # минимальный рейтинг (1-5); None/0 — не фильтровать
    min_rating: int | None = None

    # пользовательские теги (задача: пользовательские теги);
    # None — не фильтровать; иначе генерация должна содержать ВСЕ
    # перечисленные теги (без учёта регистра), лишние теги допускаются
    custom_tags: list[str] | None = None

    # исключения по пользовательским тегам (см. excluded_loras выше —
    # та же трёхстанная механика): None — не фильтровать; иначе
    # генерация не должна содержать НИ ОДНОГО из перечисленных тегов
    excluded_custom_tags: list[str] | None = None


class GenerationFilter:
    """Фильтрует список генераций по параметрам FilterOptions."""

    @staticmethod
    def apply(
        generations: list[Generation],
        options: FilterOptions,
    ) -> list[Generation]:
        """Возвращает НОВЫЙ список генераций, прошедших все заданные
        в options условия (исходный список не изменяется). Условия
        комбинируются через И — генерация должна пройти все сразу."""

        result = []

        search = options.search.lower().strip()

        # ---------- семантический поиск: вектор запроса считается один
        # раз на весь apply(), а не на каждую генерацию ----------

        semantic_query = options.semantic_query.strip()
        query_vec = None

        if semantic_query:

            query_bytes = embedding.compute_query_embedding(semantic_query)

            if query_bytes is not None:
                query_vec = embedding.bytes_to_array(query_bytes)
            else:
                # библиотека эмбеддингов недоступна (не установлена,
                # либо не удалось загрузить модель) — не проваливаем
                # поиск молча в "ничего не найдено", а деградируем до
                # обычного текстового AND-поиска по тем же словам
                logger.info(
                    "Семантический поиск недоступен — используется обычный "
                    "текстовый поиск по запросу '%s'", semantic_query
                )

        for gen in generations:

            # ---------- model ----------

            if options.model:

                if gen.model != options.model:
                    continue

            # ---------- sampler ----------

            if options.sampler:

                if gen.sampler != options.sampler:
                    continue

            # ---------- cfg ----------

            if options.min_cfg is not None:

                if gen.cfg < options.min_cfg:
                    continue

            if options.max_cfg is not None:

                if gen.cfg > options.max_cfg:
                    continue

            # ---------- steps ----------

            if options.min_steps is not None:

                if gen.steps < options.min_steps:
                    continue

            if options.max_steps is not None:

                if gen.steps > options.max_steps:
                    continue

            # ---------- loras ----------

            if options.loras:

                names = {
                    lora.filename.lower()
                    for lora in gen.loras
                }

                selected = {
                    name.lower()
                    for name in options.loras
                }

                # генерация должна содержать ВСЕ отмеченные LoRA;
                # дополнительные (неотмеченные) LoRA допускаются
                if not selected.issubset(names):
                    continue

            if options.excluded_loras:

                names = {
                    lora.filename.lower()
                    for lora in gen.loras
                }

                excluded = {name.lower() for name in options.excluded_loras}

                # генерация не должна содержать НИ ОДНОЙ из исключённых LoRA
                if names & excluded:
                    continue

            # ---------- custom tags ----------

            if options.custom_tags:

                gen_tags = {t.lower() for t in gen.custom_tags}
                selected_tags = {t.lower() for t in options.custom_tags}

                if not selected_tags.issubset(gen_tags):
                    continue

            if options.excluded_custom_tags:

                gen_tags = {t.lower() for t in gen.custom_tags}
                excluded_tags = {t.lower() for t in options.excluded_custom_tags}

                if gen_tags & excluded_tags:
                    continue

            # ---------- favorites ----------

            if options.favorites_only is True:

                if not gen.favorite:
                    continue

            elif options.favorites_only is False:

                if gen.favorite:
                    continue

            # ---------- rating ----------

            if options.min_rating:

                if gen.rating < options.min_rating:
                    continue

            # ---------- search ----------

            if search:

                data = [

                    gen.model,
                    gen.sampler,

                    str(gen.cfg),
                    str(gen.steps),

                    gen.timestamp,

                    gen.positive,
                    gen.negative,

                    str(gen.generation_time),
                ]

                for lora in gen.loras:

                    data.append(
                        lora.filename
                    )

                for img in gen.images:

                    data.append(
                        img.file
                    )

                    if img.seed is not None:

                        data.append(
                            str(img.seed)
                        )

                haystack = "\n".join(data).lower()

                # несколько слов ищутся через И: генерация должна
                # содержать ВСЕ слова запроса (в любых полях, в любом
                # порядке), а не запрос целиком одной подстрокой
                words = search.split()

                if not all(word in haystack for word in words):
                    continue

            # ---------- семантический поиск ----------

            if semantic_query:

                if query_vec is not None:

                    if gen.embedding is None:
                        # эмбеддинг ещё не посчитан (например, только
                        # что добавленный файл, sync_folder не успел
                        # его обработать) — не участвует в семантическом
                        # поиске, но и не считается совпадением
                        continue

                    score = embedding.cosine_similarity(query_vec, gen.embedding)

                    if logger.isEnabledFor(logging.DEBUG) and score >= SEMANTIC_SIMILARITY_THRESHOLD - 0.1:
                        # временная диагностика для подбора порога —
                        # показывает и прошедшие, и близкие к порогу
                        # непрошедшие совпадения; включается обычным
                        # DEBUG-логированием, ничего не пишет в проде
                        # по умолчанию (см. app/core/logger.py)
                        preview = (gen.positive or "")[:60].replace("\n", " ")
                        logger.debug(
                            "Semantic %s: id=%s score=%.3f '%s...'",
                            "match" if score >= SEMANTIC_SIMILARITY_THRESHOLD else "near-miss",
                            gen.id, score, preview
                        )

                    if score < SEMANTIC_SIMILARITY_THRESHOLD:
                        continue

                    gen.semantic_score = score

                else:
                    # деградация без модели эмбеддингов — см. выше
                    haystack = f"{gen.positive}\n{gen.negative}".lower()
                    words = semantic_query.lower().split()

                    if not all(word in haystack for word in words):
                        continue

            result.append(gen)

        if semantic_query and query_vec is not None:
            # при активном семантическом поиске порядок результатов —
            # по убыванию релевантности; вызывающий код (GalleryManager)
            # намеренно не применяет поверх этого обычную сортировку
            # (иначе релевантность потерялась бы), см. apply_filters()
            result.sort(key=lambda g: g.semantic_score, reverse=True)

        return result

    @staticmethod
    def rank_by_semantic_query(
        generations: list[Generation],
        semantic_query: str,
    ) -> list[Generation]:
        """Ранжирует уже отфильтрованный по остальным критериям список
        генераций по семантическому сходству с запросом — та же логика,
        что и ветка semantic_query внутри apply(), вынесенная отдельно
        для GalleryManager (задача: перенос GenerationFilter на SQL).

        Остальные условия (model/sampler/cfg/steps/loras/tags/favorites/
        rating/search) теперь фильтруются в SQL напрямую (см.
        GenerationFilterSQL и GenerationRepository.load_filtered_for_semantic)
        — векторное сходство там не выразить, так что генерации,
        прошедшие SQL-фильтры, дальше ранжируются здесь, в Python, как
        и раньше.

        Как и apply(): если модель эмбеддингов недоступна, деградирует
        до обычного текстового AND-поиска по позитив/негатив промпту,
        а не проваливается молча в "ничего не найдено". В этом случае
        (в отличие от ранжирования по сходству) порядок generations на
        входе сохраняется как есть — вызывающий код должен передать его
        уже отсортированным нужным образом (см. GenerationSorterSQL)."""

        semantic_query = semantic_query.strip()

        if not semantic_query:
            return list(generations)

        query_bytes = embedding.compute_query_embedding(semantic_query)
        query_vec = (
            embedding.bytes_to_array(query_bytes) if query_bytes is not None else None
        )

        if query_vec is None:

            logger.info(
                "Семантический поиск недоступен — используется обычный "
                "текстовый поиск по запросу '%s'", semantic_query
            )

            words = semantic_query.lower().split()

            return [
                gen for gen in generations
                if all(word in f"{gen.positive}\n{gen.negative}".lower() for word in words)
            ]

        result = []

        for gen in generations:

            if gen.embedding is None:
                # эмбеддинг ещё не посчитан — не участвует в
                # семантическом поиске, но и не считается совпадением
                continue

            score = embedding.cosine_similarity(query_vec, gen.embedding)

            if logger.isEnabledFor(logging.DEBUG) and score >= SEMANTIC_SIMILARITY_THRESHOLD - 0.1:
                preview = (gen.positive or "")[:60].replace("\n", " ")
                logger.debug(
                    "Semantic %s: id=%s score=%.3f '%s...'",
                    "match" if score >= SEMANTIC_SIMILARITY_THRESHOLD else "near-miss",
                    gen.id, score, preview
                )

            if score < SEMANTIC_SIMILARITY_THRESHOLD:
                continue

            gen.semantic_score = score
            result.append(gen)

        result.sort(key=lambda g: g.semantic_score, reverse=True)

        return result


class GenerationFilterSQL:
    """SQL-эквивалент GenerationFilter.apply (задача: перенос
    GenerationFilter/GenerationSorter на SQL — Этап 1).

    Строит фрагмент WHERE (готовый к конкатенации после уже
    существующего условия ``g.path LIKE ?``, начинается с " AND ", либо
    пустая строка, если фильтровать нечего) и список параметров — под
    все поля FilterOptions, КРОМЕ semantic_query: векторное сходство
    промпта не выразить обычным SQL-запросом, оно по-прежнему считается
    в Python (см. GenerationFilter.rank_by_semantic_query и
    GenerationRepository.load_filtered_for_semantic — кандидаты для
    ранжирования получаются уже отфильтрованными по всем ОСТАЛЬНЫМ
    условиям через build_where, чтобы ранжировать в Python приходилось
    как можно меньше строк).

    Ожидает алиасы ``g`` (generations) и ``u`` (user_data, LEFT JOIN по
    g.id = u.generation_id) — как в GenerationRepository._GENERATION_SELECT.
    """

    @staticmethod
    def build_where(options: FilterOptions) -> tuple[str, list]:

        conditions: list[str] = []
        params: list = []

        if options.model:
            conditions.append("g.model = ?")
            params.append(options.model)

        if options.sampler:
            conditions.append("g.sampler = ?")
            params.append(options.sampler)

        if options.min_cfg is not None:
            conditions.append("g.cfg >= ?")
            params.append(options.min_cfg)

        if options.max_cfg is not None:
            conditions.append("g.cfg <= ?")
            params.append(options.max_cfg)

        if options.min_steps is not None:
            conditions.append("g.steps >= ?")
            params.append(options.min_steps)

        if options.max_steps is not None:
            conditions.append("g.steps <= ?")
            params.append(options.max_steps)

        # ---------- loras: должна содержать ВСЕ отмеченные (по одному
        # EXISTS на каждое имя — де-факто И), и НИ ОДНОЙ из исключённых
        # ----------

        if options.loras:

            for name in options.loras:
                conditions.append(
                    "EXISTS (SELECT 1 FROM loras l WHERE l.generation_id = g.id "
                    "AND LOWER(l.filename) = LOWER(?))"
                )
                params.append(name)

        if options.excluded_loras:

            placeholders = ",".join("LOWER(?)" for _ in options.excluded_loras)
            conditions.append(
                f"NOT EXISTS (SELECT 1 FROM loras l WHERE l.generation_id = g.id "
                f"AND LOWER(l.filename) IN ({placeholders}))"
            )
            params.extend(options.excluded_loras)

        # ---------- пользовательские теги — та же механика, что и loras ----------

        if options.custom_tags:

            for tag in options.custom_tags:
                conditions.append(
                    "EXISTS (SELECT 1 FROM custom_tags t WHERE t.generation_id = g.id "
                    "AND LOWER(t.tag) = LOWER(?))"
                )
                params.append(tag)

        if options.excluded_custom_tags:

            placeholders = ",".join("LOWER(?)" for _ in options.excluded_custom_tags)
            conditions.append(
                f"NOT EXISTS (SELECT 1 FROM custom_tags t WHERE t.generation_id = g.id "
                f"AND LOWER(t.tag) IN ({placeholders}))"
            )
            params.extend(options.excluded_custom_tags)

        # ---------- favorites / rating ----------

        if options.favorites_only is True:
            conditions.append("COALESCE(u.favorite, 0) = 1")
        elif options.favorites_only is False:
            conditions.append("COALESCE(u.favorite, 0) = 0")

        if options.min_rating:
            conditions.append("COALESCE(u.rating, 0) >= ?")
            params.append(options.min_rating)

        # ---------- обычный текстовый поиск (несколько слов через И,
        # по тем же полям, что и в GenerationFilter.apply) ----------

        search = options.search.lower().strip()

        if search:

            for word in search.split():

                pattern = f"%{GenerationFilterSQL._escape_like(word)}%"

                word_conditions = [
                    "LOWER(g.model) LIKE ? ESCAPE '\\'",
                    "LOWER(g.sampler) LIKE ? ESCAPE '\\'",
                    "LOWER(CAST(g.cfg AS TEXT)) LIKE ? ESCAPE '\\'",
                    "LOWER(CAST(g.steps AS TEXT)) LIKE ? ESCAPE '\\'",
                    "LOWER(g.timestamp) LIKE ? ESCAPE '\\'",
                    "LOWER(g.positive) LIKE ? ESCAPE '\\'",
                    "LOWER(g.negative) LIKE ? ESCAPE '\\'",
                    "LOWER(CAST(g.generation_time AS TEXT)) LIKE ? ESCAPE '\\'",
                    "EXISTS (SELECT 1 FROM loras l WHERE l.generation_id = g.id "
                    "AND LOWER(l.filename) LIKE ? ESCAPE '\\')",
                    "EXISTS (SELECT 1 FROM images im WHERE im.generation_id = g.id "
                    "AND (LOWER(im.image_path) LIKE ? ESCAPE '\\' "
                    "OR LOWER(CAST(im.seed AS TEXT)) LIKE ? ESCAPE '\\'))",
                ]

                conditions.append("(" + " OR ".join(word_conditions) + ")")

                # 8 однопараметровых условий выше + 1 (loras) + 2 (images) = 11
                params.extend([pattern] * 11)

        if not conditions:
            return "", []

        return " AND " + " AND ".join(conditions), params

    @staticmethod
    def _escape_like(value: str) -> str:
        """См. GenerationRepository._like_escape — то же самое, отдельно
        здесь, чтобы этот модуль не зависел от repository.py (repository
        и так уже импортирует этот модуль, обратная зависимость создала
        бы цикл импортов)."""

        return (
            value.replace("\\", "\\\\")
                 .replace("%", "\\%")
                 .replace("_", "\\_")
        )
