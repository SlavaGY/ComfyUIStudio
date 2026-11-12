"""Reference wiring must stay valid when switching between one and two inputs."""

import copy

import pytest

from comfyui_studio.ieditor.backend import workflow

IMAGE1 = "ieditor/ieditor_" + "a" * 32 + ".png"
IMAGE2 = "ieditor/ieditor_" + "b" * 32 + ".jpg"


def params(images):
    return {"images": images, "prompt": "Change the cup to blue.", "seed": 42, "randomize_seed": False}


@pytest.mark.parametrize("images", [[IMAGE1], [IMAGE1, IMAGE2]])
def test_all_links_resolve_and_model_pair_stays_9b(images):
    template = workflow.load_template()
    original = copy.deepcopy(template)
    graph, seed = workflow.build_graph(template, params(images))
    assert template == original
    assert seed == 42
    for node in graph.values():
        for value in node["inputs"].values():
            if isinstance(value, list):
                assert value[0] in graph, value
    assert "9b" in graph["75:70"]["inputs"]["unet_name"]
    assert "8b" in graph["75:71"]["inputs"]["clip_name"]
    loads = [n["inputs"]["image"] for n in graph.values() if n["class_type"] == "LoadImage"]
    assert loads == images


def test_second_reference_reaches_both_guidance_paths_without_changing_output_size():
    graph, _ = workflow.build_graph(workflow.load_template(), params([IMAGE1, IMAGE2]))
    guider = graph["75:63"]["inputs"]
    for path, original in (("positive", "75:123"), ("negative", "75:121")):
        second = graph[guider[path][0]]
        assert second["class_type"] == "ReferenceLatent"
        assert second["inputs"]["conditioning"] == [original, 0]
        assert second["inputs"]["latent"] == ["ieditor:encode2", 0]
    assert graph["75:99"]["inputs"]["image"] == ["75:80", 0]
    assert graph["75:80"]["inputs"]["image"] == ["76", 0]


def test_advanced_parameters_apply_to_both_reference_sizes():
    graph, _ = workflow.build_graph(workflow.load_template(), {
        **params([IMAGE1, IMAGE2]), "megapixels": 0.5, "steps": 8, "cfg": 1.2, "sampler_name": "euler",
    })
    assert graph["75:80"]["inputs"]["megapixels"] == graph["ieditor:scale2"]["inputs"]["megapixels"] == 0.5
    assert graph["75:62"]["inputs"]["steps"] == 8
    assert graph["75:63"]["inputs"]["cfg"] == 1.2
    assert graph["75:61"]["inputs"]["sampler_name"] == "euler"


@pytest.mark.parametrize("images", [[], [IMAGE1] * 3, ["../private.png"], ["anything.png"]])
def test_invalid_references_fail_before_queueing(images):
    with pytest.raises(workflow.WorkflowValidationError):
        workflow.build_graph(workflow.load_template(), params(images))


def test_empty_prompt_is_rejected():
    with pytest.raises(workflow.WorkflowValidationError):
        workflow.build_graph(workflow.load_template(), {**params([IMAGE1]), "prompt": "  "})
