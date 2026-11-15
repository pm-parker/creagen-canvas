"""数据模型契约：表清单与 provider 默认值。"""

EXPECTED_TABLES = {
    "run_events",
    "storyboard_templates",
    "character_assets",
    "upload_records",
    "users",
    "tasks",
    "api_usage",
    "llm_budget",
    "capability_config",
    "prompts",
    "prompt_favorites",
    "storyboards",
    "storyboard_segments",
    "canvas_projects",
    "canvas_nodes",
    "canvas_connections",
    "provider_channels",
    "provider_models",
    "agent_runs",
    "agent_steps",
    "canvas_snapshots",
    "production_manifests",
}


def test_all_tables_in_metadata():
    import backend.app.models  # noqa: F401 — 注册全部表
    from backend.app.models import Base

    assert set(Base.metadata.tables.keys()) == EXPECTED_TABLES


def test_provider_channel_defaults():
    from backend.app.models import ProviderChannel

    ch = ProviderChannel(domain="llm", name="t", base_url="https://x", key_encrypted="v1:x")
    # 未 flush 实例不含列默认值，断言列定义层（生产形态一致）
    assert ProviderChannel.__table__.c.enabled.default.arg == 1
    assert ProviderChannel.__table__.c.sort_order.default.arg == 0
    assert ch.domain == "llm"


def test_provider_domain_whitelist():
    from backend.app.models.provider import PROVIDER_DOMAINS

    assert PROVIDER_DOMAINS == ("image", "video", "llm", "audio")


def test_task_lifecycle_helpers():
    from backend.app.models import Task

    t = Task(type="image", prompt="p", model="m")
    t.mark_started()
    assert t.status == "generating"
    t.mark_completed("http://x/a.png")
    assert t.status == "completed" and t.output_url == "http://x/a.png"
    t.mark_failed("boom")
    assert t.status == "failed" and t.error_msg == "boom"


def test_segment_keyframe_columns_exist():
    """M5 关键帧层：segments.keyframe_url / keyframe_task_id 列存在。"""
    from backend.app.models.storyboard import StoryboardSegment

    cols = {c.name for c in StoryboardSegment.__table__.columns}
    assert "keyframe_url" in cols
    assert "keyframe_task_id" in cols
