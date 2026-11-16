"""画布 schema。"""

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    title: str = Field("未命名画布", max_length=200)


class ProjectUpdate(BaseModel):
    title: str | None = Field(None, max_length=200)
    viewport: str | None = None
    background_mode: str | None = None


class NodeItem(BaseModel):
    id: int | None = None
    type: str
    title: str = ""
    position_x: float = 0
    position_y: float = 0
    width: int | None = None
    height: int | None = None
    metadata_json: str = "{}"


class ConnectionItem(BaseModel):
    id: int | None = None
    from_node_id: int
    to_node_id: int


class BulkNodesUpdate(BaseModel):
    nodes: list[NodeItem]
    base_revision: int | None = None  # 乐观锁（缺省=外部直写，不校验）
    # 客户端显式删除的节点 client_id（base 快照里有、但本地已删掉的节点）。
    # 服务端按此精确删除——含 Agent 建的节点（用户有权删）。
    deleted_client_ids: list[str] = Field(default_factory=list)


class BulkConnectionsUpdate(BaseModel):
    connections: list[ConnectionItem]
