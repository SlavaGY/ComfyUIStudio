"""
widgets.py
Переиспользуемые виджеты и вспомогательная валидация тегов,
зеркалящая логику utils/char_utils.py из расширения (без обращения
к folder_paths — здесь это standalone-редактор).
"""
from __future__ import annotations

import re
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Optional

import comfyui_studio.prompt_builder.theme as theme

_SUSPICIOUS_TAG_PATTERN = re.compile(r"[<>{}|\\]")
_MAX_TAG_LENGTH = 200


def validate_tags_text(tags: str) -> list[str]:
    """Возвращает список предупреждений по строке тегов (без обращения к диску)."""
    issues: list[str] = []
    if not tags:
        return issues

    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    empty_count = sum(1 for t in tags.split(",") if not t.strip())
    if empty_count:
        issues.append(f"{empty_count} пустых элементов (лишние запятые)")

    long_tags = [t for t in tag_list if len(t) > _MAX_TAG_LENGTH]
    if long_tags:
        issues.append(f"есть теги длиннее {_MAX_TAG_LENGTH} символов")

    suspicious = [t for t in tag_list if _SUSPICIOUS_TAG_PATTERN.search(t)]
    if suspicious:
        issues.append("подозрительные символы: " + ", ".join(repr(t[:40]) for t in suspicious[:3]))

    seen: set[str] = set()
    dupes: list[str] = []
    for t in tag_list:
        if t in seen and t not in dupes:
            dupes.append(t)
        seen.add(t)
    if dupes:
        issues.append("дублирующиеся теги: " + ", ".join(dupes[:5]))

    return issues


def parse_lora_entry(entry: str) -> tuple[str, float]:
    """'name:1.0' -> ('name', 1.0); 'name' -> ('name', 1.0)."""
    entry = entry.strip()
    if ":" in entry:
        name, _, strength_s = entry.rpartition(":")
        try:
            strength = float(strength_s.strip())
        except ValueError:
            strength = 1.0
        return name.strip(), strength
    return entry, 1.0


def format_lora_entry(name: str, strength: float) -> str:
    return f"{name}:{strength:g}"


class ScrollableFrame(ttk.Frame):
    """Frame с вертикальной прокруткой. Содержимое кладите в self.body."""

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.canvas = tk.Canvas(self, background=theme.BG, highlightthickness=0)
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = ttk.Frame(self.canvas)

        self.body.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.configure(yscrollcommand=self.vbar.set)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        for widget in (self.canvas, self.body):
            widget.bind("<Enter>", self._bind_wheel)
            widget.bind("<Leave>", self._unbind_wheel)

    def _on_canvas_resize(self, event):
        self.canvas.itemconfig(self._window, width=event.width)

    def _bind_wheel(self, _event):
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        self.canvas.bind_all("<Button-4>", self._on_wheel)
        self.canvas.bind_all("<Button-5>", self._on_wheel)

    def _unbind_wheel(self, _event):
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _on_wheel(self, event):
        if getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        else:
            delta = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(delta, "units")

    def apply_theme_colors(self):
        """Перекрашивает classic-tk Canvas под текущую тему (ttk.Frame/Scrollbar
        обновляются сами через стиль)."""
        self.canvas.configure(background=theme.BG)


class LoraListEditor(ttk.Frame):
    """Редактор списка LoRA вида ["name:strength", ...] для опций блока."""

    def __init__(self, master, on_change: Optional[Callable[[], None]] = None, **kwargs):
        super().__init__(master, **kwargs)
        self.on_change = on_change

        columns = ("name", "strength")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=4, selectmode="browse")
        self.tree.heading("name", text="LoRA")
        self.tree.heading("strength", text="Сила")
        self.tree.column("name", width=220, anchor="w")
        self.tree.column("strength", width=60, anchor="center")
        self.tree.grid(row=0, column=0, columnspan=4, sticky="nsew", pady=(0, 4))

        self.name_var = tk.StringVar()
        self.strength_var = tk.StringVar(value="1.0")

        ttk.Entry(self, textvariable=self.name_var, width=26).grid(row=1, column=0, sticky="ew", padx=(0, 4))
        ttk.Entry(self, textvariable=self.strength_var, width=6).grid(row=1, column=1, sticky="w", padx=(0, 4))
        ttk.Button(self, text="+ Добавить", command=self._add).grid(row=1, column=2, padx=(0, 4))
        ttk.Button(self, text="Удалить", command=self._remove).grid(row=1, column=3)

        self.grid_columnconfigure(0, weight=1)

    def set_entries(self, entries: list[str]):
        self.tree.delete(*self.tree.get_children())
        for entry in entries or []:
            name, strength = parse_lora_entry(entry)
            self.tree.insert("", "end", values=(name, f"{strength:g}"))

    def get_entries(self) -> list[str]:
        result = []
        for iid in self.tree.get_children():
            name, strength = self.tree.item(iid, "values")
            result.append(format_lora_entry(name, float(strength)))
        return result

    def _add(self):
        name = self.name_var.get().strip()
        if not name:
            return
        try:
            strength = float(self.strength_var.get().strip() or "1.0")
        except ValueError:
            strength = 1.0
        self.tree.insert("", "end", values=(name, f"{strength:g}"))
        self.name_var.set("")
        self.strength_var.set("1.0")
        if self.on_change:
            self.on_change()

    def _remove(self):
        sel = self.tree.selection()
        if not sel:
            return
        self.tree.delete(*sel)
        if self.on_change:
            self.on_change()


def confirm_dialog(parent, title: str, message: str) -> bool:
    return messagebox.askyesno(title, message, parent=parent)
