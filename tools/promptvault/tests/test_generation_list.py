"""Тесты для app/ui/generation_list.py и app/ui/star_rating.py.

Требуют QT_QPA_PLATFORM=offscreen (см. tests/conftest.py и
CONTRIBUTING.md).

Запуск: QT_QPA_PLATFORM=offscreen pytest tests/test_generation_list.py -v
"""

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from app.core.generation import Generation, ImageData
from app.ui.generation_list import GenerationList


def _make_gen(i: int) -> Generation:

    return Generation(
        id=i,
        path=Path(f"/tmp/gen_{i}.json"),
        timestamp=f"t{i:05d}",
        generation_time=float(i),
        model="m",
        cfg=7.0,
        steps=20,
        sampler="s",
        positive="p",
        negative="n",
        images=[ImageData(file=f"img_{i}.png")],
    )


@pytest.fixture
def generation_list(qapp):

    gens = [_make_gen(i) for i in range(10)]

    gl = GenerationList()
    gl.resize(400, 800)
    gl.show()
    gl.set_generations(gens)
    qapp.processEvents()

    yield gl

    gl.deleteLater()


class TestSelectionMode:

    def test_extended_selection_enabled(self, generation_list):

        assert generation_list.selectionMode() == GenerationList.ExtendedSelection


class TestRatingClickDoesNotStealSelection:
    """Регрессия: клик по звезде рейтинга на НЕвыделенной карточке
    раньше "телепортировал" текущий выбор списка на эту карточку —
    QLabel не принимает mousePressEvent, и клик всплывал вверх до
    QListWidget, который интерпретировал его как клик по строке.
    См. _StarLabel.mousePressEvent."""

    def test_clicking_star_on_other_card_keeps_selection(self, generation_list, qapp):

        generation_list.setCurrentRow(5)
        qapp.processEvents()

        selected_before_id = generation_list.generations[5].id

        other_card = generation_list._active_widgets.get(2)
        assert other_card is not None

        QTest.mouseClick(other_card.rating_widget._stars[2], Qt.LeftButton)
        qapp.processEvents()

        assert generation_list.currentRow() == 5

        selected_after_id = generation_list.generations[
            generation_list.currentRow()
        ].id
        assert selected_after_id == selected_before_id

    def test_clicking_star_still_applies_rating(self, generation_list, qapp):

        generation_list.setCurrentRow(5)
        qapp.processEvents()

        other_card = generation_list._active_widgets.get(2)

        captured = {}
        generation_list.ratingChanged.connect(
            lambda gid, value: captured.setdefault("result", (gid, value))
        )

        QTest.mouseClick(other_card.rating_widget._stars[2], Qt.LeftButton)
        qapp.processEvents()

        assert captured["result"] == (other_card.generation.id, 3)

    def test_clicking_favorite_button_also_keeps_selection(self, generation_list, qapp):
        """Кнопка избранного (QPushButton) сама поглощает клик и этой
        проблеме изначально не была подвержена — фиксируем это как
        регресс-тест на случай будущих изменений виджета."""

        generation_list.setCurrentRow(5)
        qapp.processEvents()

        other_card = generation_list._active_widgets.get(2)

        QTest.mouseClick(other_card.favorite_btn, Qt.LeftButton)
        qapp.processEvents()

        assert generation_list.currentRow() == 5

    def test_clicking_card_body_selects_it(self, generation_list, qapp):
        """В отличие от звезды/кнопки избранного, клик по остальной
        части карточки (превью, подписи) ДОЛЖЕН выбирать эту
        генерацию — это стандартное, ожидаемое поведение списка."""

        generation_list.setCurrentRow(5)
        qapp.processEvents()

        other_card = generation_list._active_widgets.get(2)

        QTest.mouseClick(other_card.preview, Qt.LeftButton)
        qapp.processEvents()

        assert generation_list.currentRow() == 2

    @pytest.mark.parametrize("selected_row,clicked_row", [
        (0, 3), (3, 0), (5, 1), (1, 8), (8, 5), (9, 0), (0, 9),
    ])
    def test_full_rebuild_preserves_selection_across_positions(
        self, qapp, selected_row, clicked_row
    ):
        """Регрессия на баг с "прыжками на 1-2 строку": после полной
        пересборки списка (set_generations, как это делает
        GalleryManager.apply_filters) текущий выбор должен сохраняться
        независимо от того, какая строка была выбрана и по какой
        карточке кликнули — раньше результат зависел от ПОЗИЦИИ в
        списке, а не от намерения пользователя."""

        gens = [_make_gen(i) for i in range(10)]

        gl = GenerationList()
        gl.resize(400, 800)
        gl.show()
        gl.set_generations(gens)
        qapp.processEvents()

        gl.setCurrentRow(selected_row)
        qapp.processEvents()

        selected_id_before = gl.generations[selected_row].id

        clicked_card = gl._active_widgets.get(clicked_row)
        assert clicked_card is not None

        QTest.mouseClick(clicked_card.favorite_btn, Qt.LeftButton)
        qapp.processEvents()

        # эмулируем то, что делает MainWindow._on_generations_changed
        # после debounce: полная пересборка списка + явное
        # восстановление выбора по id
        gl.set_generations(gens)
        restored_index = next(
            i for i, g in enumerate(gens) if g.id == selected_id_before
        )
        gl.setCurrentRow(restored_index)
        qapp.processEvents()

        assert gl.currentRow() == restored_index
        assert gl.generations[gl.currentRow()].id == selected_id_before

        gl.deleteLater()


class TestSetGenerationsAtomicSelectionRestore:
    """Регрессия: раньше set_generations() пересобирала список, а
    восстановление выбора (setCurrentRow) делалось отдельным вызовом
    снаружи, уже после разблокировки сигналов — список ненадолго
    оказывался без корректного выделения. set_generations(current_id=)
    делает обе операции одной атомарной, ещё заблокированной, секцией."""

    def test_current_id_restores_selection_in_a_single_call(self, qapp):

        gens = [_make_gen(i) for i in range(10)]

        gl = GenerationList()
        gl.resize(400, 800)
        gl.show()
        gl.set_generations(gens, current_id=gens[5].id)
        qapp.processEvents()

        assert gl.currentRow() == 5
        assert gl.generations[gl.currentRow()].id == gens[5].id

        gl.deleteLater()

    @pytest.mark.parametrize("selected_index,rebuild_order", [
        (5, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]),
        (5, [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]),
        (0, [3, 1, 0, 2, 4, 5, 6, 7, 8, 9]),
    ])
    def test_current_id_finds_correct_row_even_after_reordering(
        self, qapp, selected_index, rebuild_order
    ):
        """Восстановление идёт по id, а не по индексу — должно работать
        даже если пересортировка успела поменять порядок строк между
        пересборками (именно так себя ведёт GalleryManager.apply_filters
        при смене режима сортировки)."""

        gens = [_make_gen(i) for i in range(10)]

        gl = GenerationList()
        gl.resize(400, 800)
        gl.show()
        gl.set_generations(gens, current_id=gens[selected_index].id)
        qapp.processEvents()

        selected_id = gens[selected_index].id

        reordered = [gens[i] for i in rebuild_order]
        gl.set_generations(reordered, current_id=selected_id)
        qapp.processEvents()

        assert gl.generations[gl.currentRow()].id == selected_id

        gl.deleteLater()

    def test_no_current_id_leaves_selection_unset(self, qapp):

        gens = [_make_gen(i) for i in range(5)]

        gl = GenerationList()
        gl.resize(400, 800)
        gl.show()
        gl.set_generations(gens)
        qapp.processEvents()

        assert gl.currentRow() == -1

        gl.deleteLater()

    def test_unknown_current_id_leaves_selection_unset(self, qapp):

        gens = [_make_gen(i) for i in range(5)]

        gl = GenerationList()
        gl.resize(400, 800)
        gl.show()
        gl.set_generations(gens, current_id=999999)
        qapp.processEvents()

        assert gl.currentRow() == -1

        gl.deleteLater()

    def test_restoring_selection_during_rebuild_does_not_leak_a_signal(self, qapp):
        """Восстановление выбора происходит ВНУТРИ заблокированной
        секции — снаружи не должно быть видно ни одного
        generationSelected, даже для той самой строки, куда выбор
        восстанавливается."""

        gens = [_make_gen(i) for i in range(10)]

        gl = GenerationList()
        gl.resize(400, 800)
        gl.show()
        gl.set_generations(gens, current_id=gens[3].id)
        qapp.processEvents()

        captured = []
        gl.generationSelected.connect(captured.append)

        gl.set_generations(gens, current_id=gens[7].id)
        qapp.processEvents()

        assert captured == []
        assert gl.currentRow() == 7

        gl.deleteLater()


class TestContextMenuOpenActions:
    """Задача: "Open JSON" (перенесено из тулбара) и "Open in folder"
    в контекстном меню, с поддержкой массового выделения."""

    def test_menu_includes_open_json_and_open_in_folder(self, generation_list):

        menu = generation_list._build_context_menu([1])

        action_texts = [a.text() for a in menu.actions()]

        assert any("Open JSON" in t for t in action_texts)
        assert any("Open in folder" in t for t in action_texts)

    def test_open_json_emits_all_selected_ids(self, generation_list):

        menu = generation_list._build_context_menu([1, 2, 3])

        received = []
        generation_list.openJsonRequested.connect(received.append)

        open_json_action = next(a for a in menu.actions() if "Open JSON" in a.text())
        open_json_action.trigger()

        assert received == [[1, 2, 3]]

    def test_open_in_folder_emits_all_selected_ids(self, generation_list):

        menu = generation_list._build_context_menu([4, 5])

        received = []
        generation_list.openInFolderRequested.connect(received.append)

        open_folder_action = next(a for a in menu.actions() if "Open in folder" in a.text())
        open_folder_action.trigger()

        assert received == [[4, 5]]

    def test_single_selection_shows_count_of_one(self, generation_list):

        menu = generation_list._build_context_menu([1])

        action_texts = [a.text() for a in menu.actions()]

        assert any("Open JSON (1)" in t for t in action_texts)
        assert any("Open in folder (1)" in t for t in action_texts)

    def test_multi_selection_shows_count(self, generation_list):

        menu = generation_list._build_context_menu([1, 2, 3])

        action_texts = [a.text() for a in menu.actions()]

        assert any("Open JSON (3)" in t for t in action_texts)
        assert any("Open in folder (3)" in t for t in action_texts)


class TestContextMenuAddTags:
    """Задача: пользовательские теги, поддержка массового выделения —
    пункт "Add tag(s)..." в контекстном меню."""

    def test_menu_includes_add_tags_action(self, generation_list):

        menu = generation_list._build_context_menu([1])

        action_texts = [a.text() for a in menu.actions()]
        assert any("Add tag(s)" in t for t in action_texts)

    def test_add_tags_emits_all_selected_ids(self, generation_list):

        menu = generation_list._build_context_menu([1, 2, 3])

        received = []
        generation_list.addTagsRequested.connect(received.append)

        add_tags_action = next(a for a in menu.actions() if "Add tag(s)" in a.text())
        add_tags_action.trigger()

        assert received == [[1, 2, 3]]

    def test_single_selection_shows_count_of_one(self, generation_list):

        menu = generation_list._build_context_menu([1])

        action_texts = [a.text() for a in menu.actions()]
        assert any("Add tag(s) (1)" in t for t in action_texts)


class TestContextMenuBulkEdit:
    """Задача: массовое редактирование метаданных — пункт "Bulk edit
    metadata..." вместо "Edit metadata..." при выделении больше одной
    генерации."""

    def test_single_selection_shows_edit_metadata_not_bulk(self, generation_list):

        menu = generation_list._build_context_menu([1])

        action_texts = [a.text() for a in menu.actions()]

        assert any(t == "Edit metadata..." for t in action_texts)
        assert not any("Bulk edit metadata" in t for t in action_texts)

    def test_multi_selection_shows_bulk_edit_not_single(self, generation_list):

        menu = generation_list._build_context_menu([1, 2, 3])

        action_texts = [a.text() for a in menu.actions()]

        assert any("Bulk edit metadata (3)" in t for t in action_texts)
        assert not any(t == "Edit metadata..." for t in action_texts)

    def test_bulk_edit_emits_all_selected_ids(self, generation_list):

        menu = generation_list._build_context_menu([4, 5])

        received = []
        generation_list.bulkEditRequested.connect(received.append)

        bulk_action = next(a for a in menu.actions() if "Bulk edit metadata" in a.text())
        bulk_action.trigger()

        assert received == [[4, 5]]


class TestVirtualPagination:
    """set_page/append_generations/moreNeeded (задача: настоящая
    виртуальная пагинация) — total_count может быть больше, чем уже
    подгруженный префикс generations; недостающие строки — заглушки,
    подгружаемые по требованию."""

    def test_set_page_creates_placeholder_rows_for_total_count(self, qapp):

        gens = [_make_gen(i) for i in range(3)]

        gl = GenerationList()
        gl.resize(400, 800)
        gl.show()
        gl.set_page(gens, total_count=10)
        qapp.processEvents()

        try:
            assert gl.count() == 10
            assert gl.total_count() == 10
            assert len(gl.generations) == 3
        finally:
            gl.deleteLater()

    def test_set_generations_has_no_unloaded_rows(self, generation_list):
        """set_generations — псевдоним set_page(..., total_count=len(generations))."""

        assert generation_list.total_count() == len(generation_list.generations)

    def test_more_needed_emitted_when_page_incomplete_and_visible(self, qapp):

        gens = [_make_gen(i) for i in range(2)]

        gl = GenerationList()
        gl.resize(400, 800)
        gl.show()

        received = []
        gl.moreNeeded.connect(lambda: received.append(True))

        gl.set_page(gens, total_count=5)
        qapp.processEvents()

        try:
            assert received == [True]
        finally:
            gl.deleteLater()

    def test_more_needed_not_emitted_when_nothing_more_to_load(self, qapp):

        gens = [_make_gen(i) for i in range(3)]

        gl = GenerationList()
        gl.resize(400, 800)
        gl.show()

        received = []
        gl.moreNeeded.connect(lambda: received.append(True))

        gl.set_page(gens, total_count=3)
        qapp.processEvents()

        try:
            assert received == []
        finally:
            gl.deleteLater()

    def test_more_needed_not_repeated_until_append(self, qapp):

        gens = [_make_gen(i) for i in range(2)]

        gl = GenerationList()
        gl.resize(400, 800)
        gl.show()

        received = []
        gl.moreNeeded.connect(lambda: received.append(True))

        gl.set_page(gens, total_count=5)
        qapp.processEvents()

        # повторная пересборка видимых карточек ДО подгрузки — не
        # должна привести к повторному сигналу
        gl.update_visible_cards()
        gl.update_visible_cards()

        try:
            assert received == [True]
        finally:
            gl.deleteLater()

    def test_append_generations_extends_loaded_prefix(self, qapp):

        gens = [_make_gen(i) for i in range(2)]

        gl = GenerationList()
        gl.resize(400, 800)
        gl.show()
        gl.set_page(gens, total_count=5)
        qapp.processEvents()

        more = [_make_gen(i) for i in range(2, 5)]
        gl.append_generations(more)
        qapp.processEvents()

        try:
            assert [g.id for g in gl.generations] == [0, 1, 2, 3, 4]
        finally:
            gl.deleteLater()

    def test_append_generations_allows_more_needed_again(self, qapp):

        gens = [_make_gen(i) for i in range(2)]

        gl = GenerationList()
        gl.resize(400, 800)
        gl.show()

        received = []
        gl.moreNeeded.connect(lambda: received.append(True))

        gl.set_page(gens, total_count=6)
        qapp.processEvents()

        gl.append_generations([_make_gen(2), _make_gen(3)])
        qapp.processEvents()

        try:
            # после первой подгрузки (4 из 6) всё ещё есть, что грузить
            # дальше — должен запроситься снова
            assert received == [True, True]
        finally:
            gl.deleteLater()

    def test_selected_ids_excludes_not_yet_loaded_rows(self, qapp):

        gens = [_make_gen(i) for i in range(2)]

        gl = GenerationList()
        gl.resize(400, 800)
        gl.show()
        gl.set_page(gens, total_count=5)
        qapp.processEvents()

        gl.selectAll()

        try:
            # строки 2..4 ещё не подгружены — не должны попасть в выбор
            assert set(gl.selected_ids()) == {0, 1}
        finally:
            gl.deleteLater()
