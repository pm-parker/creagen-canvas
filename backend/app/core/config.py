"""应用配置：pydantic-settings，必填变量缺失时启动即失败（fail-fast）。

derived from creagen prod app/config.py @ 2026-09-27，重写为 Settings 模式。
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- 基础设施（必填，缺失启动即报错并指明变量名） ---
    database_url: str = Field(..., alias="DATABASE_URL")
    redis_url: str = Field(..., alias="REDIS_URL")
    # ComfyUI 队列看护（2026-10-09 卡死事故闭环）：逗号分隔应急端口，空=关闭
    comfyui_watch_urls: str = ""
    comfyui_stuck_seconds: int = 1800
    jwt_secret: str = Field(..., min_length=16, alias="JWT_SECRET")
    encryption_key: str = Field(..., min_length=16, alias="ENCRYPTION_KEY")

    # --- 业务开关 ---
    register_mode: str = Field("invite", alias="REGISTER_MODE")  # invite | open
    invite_codes: str = Field("", alias="INVITE_CODES")  # 逗号分隔
    enable_fake_provider: bool = Field(False, alias="ENABLE_FAKE_PROVIDER")
    access_token_expire_minutes: int = Field(60 * 24 * 7, alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    # 本服务对外基址（本地 H3 i2v：GPU 机器经此拉取 /outputs 图；空=禁用 i2v URL 模式）
    public_base_url: str = Field("", alias="PUBLIC_BASE_URL")
    # P2-14: CORS 白名单（逗号分隔；空或 * = 全放行，自托管向后兼容）
    cors_origins_raw: str = Field("", alias="CORS_ORIGINS")
    # 审计批次二（P2）：每用户上传滚动配额（字节）——封堵磁盘填充向量
    upload_quota_bytes: int = Field(5 * 1024 * 1024 * 1024, alias="UPLOAD_QUOTA_BYTES")

    @property
    def cors_origins(self) -> list[str]:
        raw = self.cors_origins_raw.strip()
        if not raw or raw == "*":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]

    @property
    def invite_code_list(self) -> list[str]:
        if not self.invite_codes:
            return []
        return [c.strip() for c in self.invite_codes.split(",") if c.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # 运行时从环境变量读取
