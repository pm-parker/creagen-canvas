"""制片清单（Production Manifest）——画布工作流的准入闸门组件。



一个项目（一集/一部剧）至多一份 manifest。槽位长满 + 探针裁决完成，

项目才算 Ready；strict 模式下 generate(video)/compose 会被 422 拦截。



设计来源（2026-10-26）：

- Kitsu: Ready 状态硬语义 + Status Automation 级联 + per-shot Casting

- ComfyUI/n8n: 提交时校验缺必填输入

- 《周三》V1-V3 实测教训: 台词探针未做/资产版本错用/道具缺席/黑名单空间词

"""



from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Text, func

from backend.app.models.base import Base


class ProductionManifest(Base):

    __tablename__ = "production_manifests"



    id = Column(Integer, primary_key=True, autoincrement=True)

    # 一项目一清单（UPSERT 语义）

    project_id = Column(

        Integer, ForeignKey("canvas_projects.id", ondelete="CASCADE"),

        nullable=False, unique=True, index=True,

    )

    user_id = Column(Integer, nullable=False, index=True)



    # 槽位定义（模板）: [{key,label,kind,required,desc}]

    # kind: "text"(文本/剧本) | "asset"(图片资产,值=节点id)
    #     | "file"(音频/成品文件,值=/outputs/路径) | "enum"(枚举)

    schema_json = Column(Text, nullable=False, default="[]")

    # 槽位状态: {key: {status: empty|filled|passed|failed, value, node_id, note, updated_at}}

    slots_json = Column(Text, nullable=False, default="{}")

    # 探针结果: {dialogue_route: "native"|"tts"|null, style_ok, identity_ok, notes}

    probes_json = Column(Text, nullable=False, default="{}")



    # 硬闸开关：True=generate(video)/compose 前强制校验；False=只记录不拦截

    strict = Column(Boolean, nullable=False, default=True)



    created_at = Column(DateTime, default=func.now())

    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())



    def to_dict(self) -> dict:

        import json

        from backend.app.core.times import iso_utc



        try:

            schema = json.loads(self.schema_json or "[]")

        except (ValueError, TypeError):

            schema = []

        try:

            slots = json.loads(self.slots_json or "{}")

        except (ValueError, TypeError):

            slots = {}

        try:

            probes = json.loads(self.probes_json or "{}")

        except (ValueError, TypeError):

            probes = {}

        return {

            "id": self.id,

            "project_id": self.project_id,

            "schema": schema,

            "slots": slots,

            "probes": probes,

            "strict": bool(self.strict),

            "created_at": iso_utc(self.created_at),

            "updated_at": iso_utc(self.updated_at),

        }
