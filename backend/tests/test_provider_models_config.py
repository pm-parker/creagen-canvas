"""模型配置升级批次：provider_models 扩列（参数/元数据/正交计价）。

spec: docs/superpowers/specs/2026-10-07-model-config.md
plan: docs/superpowers/plans/2026-10-07-model-config.md Task 1
"""


def test_pricing_units_enum():
    from backend.app.models.provider import PRICING_UNITS

    assert PRICING_UNITS == (
        "per_image",
        "per_second",
        "per_character",
        "per_token_1m",
        "per_request",
    )


def test_provider_model_new_columns_and_dict(db_session):
    from backend.app.models.provider import ProviderModel

    m = ProviderModel(channel_id=1, model_id="m1")
    db_session.add(m)
    db_session.flush()
    d = m.to_dict()
    assert d["params_json"] == {}
    assert d["meta_json"] == {}
    assert d["is_free"] is False
    assert d["pricing_unit"] is None
    assert d["price_input"] is None
    assert d["price_output"] is None


def test_provider_model_bad_json_tolerated():
    """坏 JSON 容错：to_dict 回 {} 不炸（Review Focus 2 的读取侧防线）。"""
    from backend.app.models.provider import ProviderModel

    m = ProviderModel(channel_id=1, model_id="m2", params_json="not-json{", meta_json=None)
    d = m.to_dict()
    assert d["params_json"] == {}
    assert d["meta_json"] == {}


def test_provider_model_configured_roundtrip(db_session):
    from backend.app.models.provider import ProviderModel

    m = ProviderModel(
        channel_id=1,
        model_id="m3",
        params_json='{"size": "2048x2048"}',
        meta_json='{"vision": true}',
        is_free=1,
        pricing_unit="per_image",
        price_input=0.5,
    )
    db_session.add(m)
    db_session.flush()
    d = m.to_dict()
    assert d["params_json"] == {"size": "2048x2048"}
    assert d["meta_json"] == {"vision": True}
    assert d["is_free"] is True
    assert d["pricing_unit"] == "per_image"
    assert d["price_input"] == 0.5
