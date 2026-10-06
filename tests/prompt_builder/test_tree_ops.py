"""
Тесты prompt_builder/tree_ops.py (этап R9 плана рефакторинга).

Операции над сырым деревом блоков вынесены из PromptBuilderTab и не
зависят от Qt, поэтому файл запускается без PySide6. Часть тестов
фиксирует ПРЕЖНЕЕ поведение вкладки, включая странности (помечены
``known_quirk``): R9 поведение не меняет, а перевернуть такой тест
можно только осознанным решением.
"""
import ast
from pathlib import Path

from comfyui_studio.prompt_builder import tree_ops


def _sample():
    """root: [group G [cat A (2 opts), cat T (free_text)], cat B (0 opts)]"""
    opt1 = {"label": "o1", "tags": "t1"}
    opt2 = {"label": "o2", "tags": "t2"}
    cat_a = {"id": "a", "label": "A", "type": "multi_select", "options": [opt1, opt2]}
    cat_t = {"id": "t", "label": "T", "type": "free_text"}
    group = {"id": "g", "label": "G", "type": "group", "children": [cat_a, cat_t]}
    cat_b = {"id": "b", "label": "B", "type": "single_select", "options": []}
    root = [group, cat_b]
    return root, group, cat_a, cat_t, cat_b, opt1, opt2


def _info(kind, node, parent_list):
    return {"kind": kind, "node": node, "parent_list": parent_list}


# -- модуль не зависит от Qt ---------------------------------------------


def test_module_does_not_import_qt():
    src = Path(tree_ops.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith(("PySide6", "tkinter")) for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith(("PySide6", "tkinter"))


# -- find_owner_category ---------------------------------------------------


def test_find_owner_category_for_option_in_group():
    root, group, cat_a, _t, _b, opt1, _o2 = _sample()
    owner = tree_ops.find_owner_category(root, cat_a["options"])
    assert owner is not None
    assert owner["kind"] == "cat"
    assert owner["node"] is cat_a
    assert owner["parent_list"] is group["children"]


def test_find_owner_category_for_top_level_category():
    root, _g, _a, _t, cat_b, _o1, _o2 = _sample()
    owner = tree_ops.find_owner_category(root, cat_b["options"])
    assert owner is not None
    assert owner["node"] is cat_b
    assert owner["parent_list"] is root


def test_find_owner_category_matches_by_identity_not_equality():
    # Два блока с равными (пустыми) списками вариантов: нужен именно тот,
    # чей список передали.
    first = {"id": "1", "type": "multi_select", "options": []}
    second = {"id": "2", "type": "multi_select", "options": []}
    root = [first, second]
    assert tree_ops.find_owner_category(root, second["options"])["node"] is second
    assert tree_ops.find_owner_category(root, first["options"])["node"] is first


def test_find_owner_category_unknown_list_returns_none():
    root, *_ = _sample()
    assert tree_ops.find_owner_category(root, []) is None


def test_find_owner_category_does_not_mutate_tree():
    root, group, *_ = _sample()
    del group["children"]  # группа без children не должна получить ключ от поиска
    tree_ops.find_owner_category(root, [])
    assert "children" not in group


# -- current_container_for_add --------------------------------------------


def test_container_without_selection_is_root():
    root, *_ = _sample()
    assert tree_ops.current_container_for_add(root, None) is root


def test_container_for_group_is_its_children_and_creates_missing_key():
    root, group, *_ = _sample()
    assert tree_ops.current_container_for_add(root, _info("group", group, root)) is group["children"]
    empty = {"id": "e", "type": "group"}
    assert tree_ops.current_container_for_add([empty], _info("group", empty, [empty])) == []
    assert empty["children"] == []  # как раньше: setdefault


def test_container_for_category_is_list_it_lives_in():
    root, group, cat_a, *_ = _sample()
    info = _info("cat", cat_a, group["children"])
    assert tree_ops.current_container_for_add(root, info) is group["children"]


def test_container_for_option_is_list_of_its_owner_category():
    root, group, cat_a, _t, _b, opt1, _o2 = _sample()
    info = _info("opt", opt1, cat_a["options"])
    assert tree_ops.current_container_for_add(root, info) is group["children"]


def test_container_for_orphan_option_falls_back_to_root():
    root, *_ = _sample()
    info = _info("opt", {"label": "x"}, [])
    assert tree_ops.current_container_for_add(root, info) is root


# -- options_list_for_add --------------------------------------------------


def test_options_list_no_selection():
    assert tree_ops.options_list_for_add(None) == (None, "no_selection")


def test_options_list_for_option_is_its_siblings():
    _root, _g, cat_a, _t, _b, opt1, _o2 = _sample()
    lst, why = tree_ops.options_list_for_add(_info("opt", opt1, cat_a["options"]))
    assert lst is cat_a["options"] and why is None


def test_options_list_for_category_is_its_options_and_creates_missing_key():
    root, *_ = _sample()
    bare = {"id": "x", "type": "single_select"}
    lst, why = tree_ops.options_list_for_add(_info("cat", bare, root))
    assert lst == [] and why is None
    assert bare["options"] is lst


def test_options_list_refuses_free_text_and_group():
    root, group, _a, cat_t, *_ = _sample()
    assert tree_ops.options_list_for_add(_info("cat", cat_t, group["children"])) == (None, "free_text")
    assert tree_ops.options_list_for_add(_info("group", group, root)) == (None, "group")


# -- конструкторы узлов ----------------------------------------------------


def test_new_nodes_have_expected_shape_and_key_order():
    g = tree_ops.new_group_node("Новая группа")
    assert list(g) == ["id", "label", "type", "children"]
    assert g["type"] == "group" and g["children"] == [] and g["id"].startswith("group_")
    c = tree_ops.new_category_node("Новый блок")
    assert list(c) == ["id", "label", "type", "max_random", "options"]
    assert c["type"] == "multi_select" and c["max_random"] == 0 and c["id"].startswith("block_")
    o = tree_ops.new_option_node("Новый вариант")
    assert o == {"label": "Новый вариант", "tags": ""}
    assert list(o) == ["label", "tags"]


# -- remove_node -----------------------------------------------------------


def test_remove_node_removes_and_reports():
    root, group, cat_a, *_ = _sample()
    assert tree_ops.remove_node(_info("cat", cat_a, group["children"])) is True
    assert cat_a not in group["children"]


def test_remove_node_missing_is_swallowed():
    root, group, cat_a, *_ = _sample()
    ghost = {"id": "ghost"}
    assert tree_ops.remove_node(_info("cat", ghost, group["children"])) is False
    assert len(group["children"]) == 2


def test_remove_node_known_quirk_removes_first_equal_dict_not_selected_one():
    # Прежнее поведение: list.remove ищет по равенству. Два одинаковых
    # варианта -- удаляется первый, даже если выбран второй. Содержимое
    # у них одинаковое, поэтому заметно это только по числу элементов.
    a = {"label": "same", "tags": ""}
    b = {"label": "same", "tags": ""}
    options = [a, b]
    tree_ops.remove_node(_info("opt", b, options))
    assert len(options) == 1
    assert options[0] is b  # остался выбранный; удалён первый


# -- move_node -------------------------------------------------------------


def test_move_node_down_and_up():
    lst = [{"id": "1"}, {"id": "2"}, {"id": "3"}]
    first = lst[0]
    assert tree_ops.move_node(_info("cat", first, lst), 1) is True
    assert [n["id"] for n in lst] == ["2", "1", "3"]
    assert tree_ops.move_node(_info("cat", first, lst), -1) is True
    assert [n["id"] for n in lst] == ["1", "2", "3"]


def test_move_node_at_boundary_does_nothing():
    lst = [{"id": "1"}, {"id": "2"}]
    assert tree_ops.move_node(_info("cat", lst[0], lst), -1) is False
    assert tree_ops.move_node(_info("cat", lst[1], lst), 1) is False
    assert [n["id"] for n in lst] == ["1", "2"]


def test_move_node_known_quirk_equal_dicts_use_first_index():
    # Прежнее поведение: lst.index(node) ищет по равенству. Для двух
    # одинаковых вариантов "сдвиг" второго вниз берёт индекс первого и
    # просто меняет местами равные словари -- порядок содержимого не
    # меняется, хотя функция сообщает об изменении.
    a = {"label": "same", "tags": ""}
    b = {"label": "same", "tags": ""}
    c = {"label": "other", "tags": ""}
    lst = [a, b, c]
    assert tree_ops.move_node(_info("opt", b, lst), 1) is True
    assert lst[0] is b and lst[1] is a and lst[2] is c  # c на месте, b не прошёл мимо a
