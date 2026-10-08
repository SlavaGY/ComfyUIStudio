"""Сборка графа Imagine Pony из шаблона (assets/workflow_template.json)."""

import json

from comfyui_studio.imagine_pony.backend import workflow


def _params(**over):
    p = {
        "characters": {"selected": ["rimuru_tempest"], "excluded": ["blue_hair"]},
        "builder": {
            "state": {"quality_prefix": "Maximum", "background": "Арктика"},
            "negative_preset": "NSFW",
            "extra_negative": "box",
            "excluded_negative": [],
        },
        "loras": [{"file": "pony\\detail_xl.safetensors", "strength": 0.6}],
        "checkpoint": "other.safetensors",
        "aspect_ratio": "1:1 (Square)",
        "megapixels": 1.0,
        "batch_size": 2,
        "steps_min": 20,
        "steps_max": 40,
        "cfg": 6.5,
        "sampler_name": "euler",
        "seed": 5,
        "randomize_seed": False,
    }
    p.update(over)
    return p


def _build(**over):
    return workflow.build_graph(workflow.load_template(), _params(**over))


def test_character_and_builder_inputs_are_json_strings():
    graph, _ = _build()
    chars = graph["120"]["inputs"]
    assert json.loads(chars["selected_names"]) == ["rimuru_tempest"]
    assert json.loads(chars["excluded_tags"]) == ["blue_hair"]
    b = graph["121"]["inputs"]
    state = json.loads(b["state_json"])
    assert state["background"] == "Арктика" and state["negative_preset"] == "NSFW"
    assert b["extra_negative"] == "box" and b["negative_preset"] == "NSFW"


def test_manual_lora_slots_reset_unused_and_keep_link():
    graph, _ = _build()
    lora = graph["123"]["inputs"]
    assert lora["lora_1"] == "pony\\detail_xl.safetensors" and lora["strength_1"] == 0.6
    assert all(lora[f"lora_{i}"] == "none" for i in range(2, 6))
    assert lora["lora_list"] == ["121", 2]  # лоры персонажей/билдера по-прежнему приходят ссылкой


def test_fixed_seed_and_sampler():
    graph, seed = _build()
    assert seed == 5 and graph["88"]["inputs"]["noise_seed"] == 5
    assert graph["88"]["inputs"]["cfg"] == 6.5
    assert graph["138"]["inputs"]["sampler_name"] == "euler"


def test_steps_range_uses_random_number_node_with_fresh_seed():
    g1, _ = _build()
    g2, _ = _build()
    r = g1["141"]["inputs"]
    assert (r["minimum"], r["maximum"]) == (20, 40)
    assert g1["137"]["inputs"]["steps"] == ["141", 2]
    # seed Random Number меняется -- иначе ComfyUI отдаст результат из кэша
    assert r["seed"] != g2["141"]["inputs"]["seed"]


def test_equal_steps_become_fixed_number():
    graph, _ = _build(steps_min=25, steps_max=25)
    assert graph["137"]["inputs"]["steps"] == 25


def test_reversed_range_is_sorted():
    graph, _ = _build(steps_min=40, steps_max=20)
    r = graph["141"]["inputs"]
    assert (r["minimum"], r["maximum"]) == (20, 40)


def test_negative_override_replaces_link():
    graph, _ = workflow.build_graph(workflow.load_template(), _params(), negative_override="lowres")
    assert graph["129"]["inputs"]["text"] == "lowres"


def test_negative_link_kept_without_override():
    graph, _ = _build()
    assert graph["129"]["inputs"]["text"] == ["121", 1]


def test_template_is_not_mutated():
    template = workflow.load_template()
    before = json.dumps(template, sort_keys=True)
    workflow.build_graph(template, _params())
    assert json.dumps(template, sort_keys=True) == before
