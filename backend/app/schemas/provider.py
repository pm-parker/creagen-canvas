"""provider 渠道 schema：响应模型显式排除任何 key 字段。"""

from pydantic import BaseModel, Field


class ChannelCreateRequest(BaseModel):
    domain: str = Field(..., description='渠道域: "image" | "video" | "llm" | "audio"')
    name: str = Field(..., min_length=1, max_length=100)
    base_url: str = Field(..., min_length=1, max_length=500)
    key: str = Field("", max_length=500)  # 空=本地推理（Ollama 等无需密钥）
    system: bool = Field(False, description="管理员创建全员共享的系统渠道")
    capabilities: list[str] = Field(
        default_factory=list, description='能力开关，如 ["edit"] 表示支持 /images/edits 局部重绘'
    )
    models: list["ModelUpsert"] | None = Field(
        None, description="创建时直配模型目录（解决 DB 直插痛点）"
    )


class ModelUpsert(BaseModel):
    """创建渠道时的模型目录项（model_id 必填，其余可选）。"""

    model_id: str = Field(..., min_length=1, max_length=100)
    display_name: str | None = Field(None, max_length=200)
    enabled: bool = True
    params_json: dict = Field(default_factory=dict)
    meta_json: dict = Field(default_factory=dict)
    is_free: bool = False
    pricing_unit: str | None = None
    price_input: float | None = None
    price_output: float | None = None


class ChannelUpdateRequest(BaseModel):
    name: str | None = None
    base_url: str | None = None
    key: str | None = None
    enabled: bool | None = None
    capabilities: list[str] | None = None


class ModelItem(BaseModel):
    id: int
    model_id: str
    display_name: str
    enabled: bool
    params_json: dict = Field(default_factory=dict)
    meta_json: dict = Field(default_factory=dict)
    is_free: bool = False
    pricing_unit: str | None = None
    price_input: float | None = None
    price_output: float | None = None


class ModelUpdateRequest(BaseModel):
    """模型级配置部分更新（spec §7）：任意子集；未传字段不动、显式 null 清除，
    合并态校验在端点内（存量配置 + patch 一起看）。
    """

    display_name: str | None = Field(None, max_length=200)
    enabled: bool | None = None
    params_json: dict | None = None
    meta_json: dict | None = None
    is_free: bool | None = None
    pricing_unit: str | None = None
    price_input: float | None = None
    price_output: float | None = None


class ChannelResponse(BaseModel):
    """渠道响应——只含 key_masked，绝不回传明文/密文。"""

    id: int
    domain: str
    name: str
    base_url: str
    key_masked: str
    enabled: bool
    sort_order: int
    user_id_owned: bool = False
    capabilities: list[str] = Field(default_factory=list)
    models: list[ModelItem] = []


class ChannelListResponse(BaseModel):
    channels: list[ChannelResponse] = []


class ChannelTestResponse(BaseModel):
    ok: bool
    latency_ms: int | None = None
    models_found: int | None = None
    code: str | None = None
    message: str | None = None
