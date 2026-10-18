"""
Режим запуска, выбираемый списком на главном экране (cfg["interface"]).

Раньше «что запускать» было радиокнопками в настройках ComfyUI и покрывало
только ComfyUI/Imagine/Imagine Pony. Теперь это один список рядом с кнопкой
«Запустить», и в него входит Ultimate RVC:

  comfyui       -- ComfyUI, открывается своим веб-интерфейсом
  imagine       -- Imagine поверх ComfyUI
  imagine_pony  -- Imagine Pony поверх ComfyUI
  rvc           -- Ultimate RVC: собственное приложение Studio (comfyui_studio/rvc/)
  rvc_gradio    -- Ultimate RVC: оригинальный Gradio-интерфейс (там есть то, чего
                   в приложении Studio пока нет, например обучение моделей)
  lm_studio     -- установленное LM Studio в отдельном окне с родным интерфейсом

Значение хранится в старом ключе cfg["interface"], поэтому прежние
config.json читаются без миграции (comfyui/imagine/imagine_pony те же).
Модуль без Qt -- им пользуются и окно, и тесты.
"""

from __future__ import annotations

COMFYUI = "comfyui"
IMAGINE = "imagine"
IMAGINE_PONY = "imagine_pony"
RVC = "rvc"
RVC_GRADIO = "rvc_gradio"
LM_STUDIO = "lm_studio"

# (значение в конфиге, подпись в списке). Порядок = порядок в списке.
MODES = (
    (COMFYUI, "ComfyUI"),
    (IMAGINE, "Imagine"),
    (IMAGINE_PONY, "Imagine Pony"),
    (RVC, "Ultimate RVC"),
    (RVC_GRADIO, "Ultimate RVC (оригинал)"),
    (LM_STUDIO, "LM Studio (оригинал)"),
)

_VALUES = tuple(value for value, _label in MODES)


def normalize_mode(value, default=COMFYUI):
    """Неизвестное/пустое значение из config.json -> режим по умолчанию
    (ComfyUI), чтобы испорченный конфиг не оставлял кнопку «Запустить»
    без поведения."""
    return value if value in _VALUES else default


def is_rvc_mode(mode):
    return mode in (RVC, RVC_GRADIO)


def is_comfy_family(mode):
    """Режимы, которым нужен запущенный ComfyUI (весь прежний путь запуска)."""
    return mode in (COMFYUI, IMAGINE, IMAGINE_PONY)


def rvc_process_kind(mode):
    """Как запускать RVC-процесс для режима: 'app' (приложение Studio) или
    'gradio' (оригинал). Для не-RVC режима -- None."""
    if mode == RVC:
        return "app"
    if mode == RVC_GRADIO:
        return "gradio"
    return None


def mode_label(mode):
    for value, label in MODES:
        if value == mode:
            return label
    return mode
