# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import pytest

from jiuwenswarm.common.model_vendor_registry import PlanKind, get_preset, to_frontend_payload


def test_frontend_payload_contains_protocol_scoped_reasoning_capabilities() -> None:
    payload = to_frontend_payload()
    alibaba = next(item for item in payload["token_plan"] if item["vendor_key"] == "alibaba")
    qwen = alibaba["reasoning_capabilities"]["qwen3.8-max"]

    assert qwen["openai"] == {
        "options": ["off", "low", "medium", "xhigh"],
        "recommended": "xhigh",
    }
    assert qwen["anthropic"] == {
        "options": ["off", "low", "medium", "xhigh"],
        "recommended": "xhigh",
    }


def test_frontend_payload_isolates_single_model_capability_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 单个模型的能力查询异常不能拖垮整个 vendors.list：该模型条目被跳过，
    # 其余模型与厂商卡片照常返回（前端回落到 reasoning_rules/model_fallbacks）。
    import jiuwenswarm.common.model_vendor_registry as registry

    real_capability = registry.get_reasoning_capability

    def _flaky(*args: object, **kwargs: object):
        if kwargs.get("model") == "qwen3.8-max":
            raise RuntimeError("boom")
        return real_capability(*args, **kwargs)

    monkeypatch.setattr(registry, "get_reasoning_capability", _flaky)
    payload = registry.to_frontend_payload()
    alibaba = next(item for item in payload["token_plan"] if item["vendor_key"] == "alibaba")

    assert "qwen3.8-max" not in alibaba["reasoning_capabilities"]
    assert alibaba["reasoning_capabilities"]


def test_frontend_payload_contains_provider_scoped_reasoning_rules() -> None:
    payload = to_frontend_payload()
    qianfan = next(item for item in payload["custom_api"] if item["vendor_key"] == "baidu")
    glm52 = next(rule for rule in qianfan["reasoning_rules"] if "glm-5.2*" in rule["patterns"])

    # Qianfan proxies GLM with a plain toggle; the cross-vendor fallback for
    # glm-5.2 exposes off/high/max instead. The preset rule must win so the
    # frontend matches the backend save validation.
    assert glm52["capabilities"]["openai"]["options"] == ["off", "on"]


def test_frontend_payload_contains_custom_model_fallbacks() -> None:
    reasoning = to_frontend_payload()["reasoning"]
    glm = next(item for item in reasoning["model_fallbacks"] if "glm-5.2*" in item["patterns"])

    assert reasoning["protocol_defaults"]["openai"]["options"] == ["off", "low", "medium", "high"]
    assert glm["capabilities"]["openai"]["options"] == ["off", "high", "max"]


def test_frontend_payload_exposes_minimax_and_volcengine_video_gen_presets() -> None:
    payload = to_frontend_payload()
    minimax = next(item for item in payload["custom_api"] if item["vendor_key"] == "minimax")
    volcengine = next(item for item in payload["custom_api"] if item["vendor_key"] == "volcengine")

    assert minimax["video_gen_default_model"] == "MiniMax-H3"
    assert minimax["video_gen_model_options"] == ["MiniMax-H3", "MiniMax-H3-Max"]
    assert minimax["video_gen_api_base"] == "https://api.minimaxi.com"
    assert minimax["endpoint_profile"] == "minimax"
    assert minimax["image_gen_default_model"] == "image-01"
    assert minimax["image_gen_model_options"] == ["image-01", "image-01-live"]
    assert minimax["image_gen_api_base"] == "https://api.minimaxi.com"

    assert volcengine["video_gen_default_model"] == "doubao-seedance-2-5-260628"
    assert volcengine["video_gen_model_options"] == [
        "doubao-seedance-2-5-260628",
        "doubao-seedance-2-0-260128",
        "doubao-seedance-2-0-fast-260128",
        "doubao-seedance-2-0-mini-260615",
    ]
    assert volcengine["video_gen_api_base"] == "https://ark.cn-beijing.volces.com/api/v3"
    assert volcengine["endpoint_profile"] == "volcengine"
    assert volcengine["image_gen_default_model"] == "doubao-seedream-5-0-260128"
    assert volcengine["image_gen_model_options"] == [
        "doubao-seedream-5-0-260128",
        "doubao-seedream-5-0-pro-260628",
        "doubao-seedream-4-5-251128",
        "doubao-seedream-4-0-250828",
    ]
    assert volcengine["image_gen_api_base"] == "https://ark.cn-beijing.volces.com/api/v3"


def test_frontend_payload_exposes_vllm_omni_generation_only_preset() -> None:
    payload = to_frontend_payload()
    vllm_omni = next(item for item in payload["custom_api"] if item["vendor_key"] == "vllm-omni")

    assert vllm_omni["endpoint_profile"] == "vllm-omni"
    assert vllm_omni["icon_key"] == "vllm-omni"
    assert vllm_omni["generation_only"] is True
    assert vllm_omni["api_base_editable"] is True
    assert vllm_omni["api_key_optional"] is True
    assert vllm_omni["model_name_optional"] is True
    assert vllm_omni["default_model"] == ""
    assert vllm_omni["model_options"] == []
    # Don't assert video_gen_model_options or image_gen_model_options because they are stubs and not actually used.
    # Generation-only presets never appear in the chat-model token plan.
    assert all(item["vendor_key"] != "vllm-omni" for item in payload["token_plan"])


def test_modelarts_presets_use_current_v2_model_ids() -> None:
    token_plan = get_preset("maas", PlanKind.TOKEN_PLAN)
    custom_api = get_preset("maas", PlanKind.CUSTOM_API)

    assert token_plan is not None
    assert token_plan.model_options == ("glm-5.1", "kimi-k2.6", "deepseek-v4-flash")
    assert custom_api is not None
    assert custom_api.default_model == "openpangu-2.0-pro"
    assert "pangu-large" not in custom_api.model_options
