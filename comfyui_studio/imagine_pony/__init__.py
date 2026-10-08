"""
Imagine Pony — второе приложение в стиле Imagine, но под API-граф PonyXL
(workflow с кастомными узлами расширения character_search_ui:
CharacterSearchUI, PromptBuilderNode, MultiLoraLoader).

От Imagine отличается тем, что:
  * нет генератора промптов (llama.cpp), дев-режима, «Стилей» и «Категорий»
    Imagine;
  * вместо них -- три панели, повторяющие интерфейсы узлов ComfyUI:
    поиск/выбор персонажей (CharacterSearchUI), сборщик промпта
    (PromptBuilderNode, со случайным выбором и вкл/выкл категорий и тегов) и
    ручные слоты LoRA (MultiLoraLoader).

Данные персонажей и конфиг билдера на стороне ComfyUI (characters.json,
prompt_builder_config.json) -- приложение берёт их через HTTP-маршруты самого
расширения (/character_search/*, /prompt_builder/*), поэтому нужная логика
(сборка тегов, рандом, валидация) не дублируется.
"""
