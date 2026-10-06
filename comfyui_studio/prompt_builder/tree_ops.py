"""
tree_ops.py
Операции над сырым деревом блоков prompt_builder_config.json, не зависящие
от Qt: куда добавить новый узел, какой список вариантов подходит, как
удалить и сдвинуть узел. Без единого импорта PySide6 — по образцу logic.py.

Вынесено из PromptBuilderTab на этапе R9 плана рефакторинга. Поведение то
же, что было в методах вкладки; единственное отличие — поиск блока-владельца
идёт по самим спискам словарей, а не обходом QTreeWidget (дерево строится
из тех же списков, поэтому результат совпадает).

Формат записи о выбранном узле ("info") тот же, что в реестре вкладки:
    {"kind": "group" | "cat" | "opt", "node": <dict>, "parent_list": <list>}
где parent_list — тот список, внутри которого лежит node.

Сравнения списков и узлов ниже намеренно такие же, как были во вкладке:
``remove`` и ``index`` ищут по РАВЕНСТВУ словарей, а не по тождеству.
Это сохранено без изменений (см. тесты ..._known_quirk): R9 не меняет
поведение редактора.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from comfyui_studio.prompt_builder.logic import new_id

Node = dict[str, Any]
NodeInfo = dict[str, Any]

# Почему вариант нельзя добавить: нет выбранного узла / блок свободного
# текста / выбрана группа. Тексты сообщений остаются в UI-слое.
AddOptionRefusal = Literal["no_selection", "free_text", "group"]


def find_owner_category(
    categories: list[Node], options_list: list[Node]
) -> Optional[NodeInfo]:
    """Ищет блок (не группу), чей список ``options`` — именно этот объект.

    Возвращает запись того же вида, что в реестре вкладки, либо None.
    Обход в глубину в порядке дерева; группы раскрываются через children.
    """
    for node in categories:
        if node.get("type", "") == "group":
            found = find_owner_category(node.get("children", []), options_list)
            if found is not None:
                return found
        elif node.get("options") is options_list:
            return {"kind": "cat", "node": node, "parent_list": categories}
    return None


def current_container_for_add(
    categories: list[Node], info: Optional[NodeInfo]
) -> list[Node]:
    """Список, в который добавляется новая группа/блок при выбранном ``info``.

    Ничего не выбрано — корень. Выбрана группа — её children. Выбран блок —
    список, в котором он лежит. Выбран вариант — список его блока-владельца
    (если владелец не нашёлся — корень).
    """
    if info is None:
        return categories
    if info["kind"] == "group":
        children: list[Node] = info["node"].setdefault("children", [])
        return children
    if info["kind"] == "opt":
        owner = find_owner_category(categories, info["parent_list"])
        if owner is None:
            return categories
        owner_list: list[Node] = owner["parent_list"]
        return owner_list
    parent_list: list[Node] = info["parent_list"]
    return parent_list


def options_list_for_add(
    info: Optional[NodeInfo],
) -> tuple[Optional[list[Node]], Optional[AddOptionRefusal]]:
    """Список вариантов, куда добавить новый вариант при выбранном ``info``.

    Возвращает (список, None) либо (None, причина отказа).
    """
    if info is None:
        return None, "no_selection"
    if info["kind"] == "opt":
        siblings: list[Node] = info["parent_list"]
        return siblings, None
    if info["kind"] == "cat":
        if info["node"].get("type") == "free_text":
            return None, "free_text"
        options: list[Node] = info["node"].setdefault("options", [])
        return options, None
    return None, "group"


def new_group_node(label: str) -> Node:
    return {"id": new_id("group"), "label": label, "type": "group", "children": []}


def new_category_node(label: str) -> Node:
    return {"id": new_id("block"), "label": label, "type": "multi_select",
            "max_random": 0, "options": []}


def new_option_node(label: str) -> Node:
    return {"label": label, "tags": ""}


def remove_node(info: NodeInfo) -> bool:
    """Убирает узел из его списка. False, если такого узла в списке нет."""
    try:
        info["parent_list"].remove(info["node"])
    except ValueError:
        return False
    return True


def move_node(info: NodeInfo, direction: int) -> bool:
    """Сдвигает узел на ``direction`` позиций внутри его списка.

    True — порядок изменён; False — выход за границы списка (ничего не
    менялось).
    """
    lst: list[Node] = info["parent_list"]
    node = info["node"]
    idx = lst.index(node)
    new_idx = idx + direction
    if 0 <= new_idx < len(lst):
        lst[idx], lst[new_idx] = lst[new_idx], lst[idx]
        return True
    return False
