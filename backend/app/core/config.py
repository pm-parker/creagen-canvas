"""应用配置：pydantic-settings，必填变量缺失时启动即失败（fail-fast）。

derived from creagen prod app/config.py @ 2026-09-27，重写为 Settings 模式。
"""

from functools import lru_cache

from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 安全密钥最低长度（openssl rand -hex 32 生成 64 字符，32 是下限）
SECRET_MIN_LENGTH = 32
# .env.example / 部署文档中出现的占位值特征（不区分大小写）：命中即拒绝启动，
# 防止示例配置被原样复制上线。特征取子串匹配，覆盖 change-me / your-secret 等变体。
_SECRET_PLACEHOLDER_MARKERS = (
    "change-me",
    "change_me",
    "changeme",
    "change-this",
    "please-change",
    "please_change",
    "replace-me",
    "replace_me",
    "replaceme",
    "your-secret",
    "your_secret",
    "insecure",
    "placeholder",
    "example-secret",
)
_FIELD_TO_ENV = {"jwt_secret": "JWT_SECRET", "encryption_key": "ENCRYPTION_KEY"}


def _is_placeholder_secret(value: str) -> bool:
    """是否命中已知占位值特征（如 .env.example 中 shipped 的 change-me 示例）。"""
    low = value.strip().lower()
    return any(marker in low for marker in _SECRET_PLACEHOLDER_MARKERS)


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
    jwt_secret: str = Field(..., alias="JWT_SECRET")
    encryption_key: str = Field(..., alias="ENCRYPTION_KEY")

    # --- 业务开关 ---
    register_mode: str = Field("invite", alias="REGISTER_MODE")  # invite | open
    # 逗号分隔；无内置默认邀请码——invite 模式未配置时注册一律拒绝（fail-closed）
    invite_codes: str = Field("", alias="INVITE_CODES")
    # 仅本地开发/CI 联调；生产必须显式设 true 才启用，默认关闭
    enable_fake_provider: bool = Field(False, alias="ENABLE_FAKE_PROVIDER")
    access_token_expire_minutes: int = Field(60 * 24 * 7, alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    # 本服务对外基址（本地 H3 i2v：GPU 机器经此拉取 /outputs 图；空=禁用 i2v URL 模式）
    public_base_url: str = Field("", alias="PUBLIC_BASE_URL")
    # P2-14: CORS 白名单（逗号分隔；空或 * = 全放行，自托管向后兼容）
    cors_origins_raw: str = Field("", alias="CORS_ORIGINS")
    # 审计批次二（P2）：每用户上传滚动配额（字节）——封堵磁盘填充向量
    upload_quota_bytes: int = Field(5 * 1024 * 1024 * 1024, alias="UPLOAD_QUOTA_BYTES")

    @field_validator("jwt_secret", "encryption_key")
    @classmethod
    def _reject_weak_secret(cls, v: str, info: ValidationInfo) -> str:
        """启动即拒绝空/过短/占位密钥（部署脚手架 footgun 封堵）。

        - 长度 < SECRET_MIN_LENGTH：熵不足；
        - 命中占位特征：.env.example 等示例值被原样复制上线。
        """
        env_name = _FIELD_TO_ENV.get(info.field_name or "", info.field_name or "?")
        stripped = v.strip()
        if len(stripped) < SECRET_MIN_LENGTH:
            raise ValueError(
                f"{env_name} 不安全：至少 {SECRET_MIN_LENGTH} 字符（当前 {len(stripped)}）。"
                "请生成随机值：openssl rand -hex 32"
            )
        if _is_placeholder_secret(stripped):
            raise ValueError(
                f"{env_name} 不安全：仍为示例占位值（检测到 change-me/please-change 等特征）。"
                "请生成随机值：openssl rand -hex 32"
            )
        return v

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
