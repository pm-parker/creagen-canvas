"""模型汇总：import 即注册全部 ORM 表（Alembic autogenerate 依赖此模块）。"""

from backend.app.models.agent_run import AgentRun, AgentStep
from backend.app.models.api_usage import APIUsage
from backend.app.models.base import Base
from backend.app.models.canvas import CanvasConnection, CanvasNode, CanvasProject
from backend.app.models.canvas_snapshot import CanvasSnapshot
from backend.app.models.capability import CapabilityConfig
from backend.app.models.character import CharacterAsset
from backend.app.models.llm_budget import LLMBudget
from backend.app.models.production import ProductionManifest
from backend.app.models.prompt import Prompt, PromptFavorite
from backend.app.models.provider import ProviderChannel, ProviderModel
from backend.app.models.run_event import RunEvent
from backend.app.models.storyboard import Storyboard, StoryboardSegment
from backend.app.models.task import Task
from backend.app.models.template import StoryboardTemplate
from backend.app.models.upload import UploadRecord
from backend.app.models.user import User

__all__ = [
    "ProductionManifest",
    "CharacterAsset",
    "StoryboardTemplate",
    "UploadRecord",
    "AgentRun",
    "AgentStep",
    "RunEvent",
    "APIUsage",
    "Base",
    "CapabilityConfig",
    "CanvasConnection",
    "CanvasSnapshot",
    "CanvasNode",
    "CanvasProject",
    "LLMBudget",
    "Prompt",
    "PromptFavorite",
    "ProviderChannel",
    "ProviderModel",
    "Storyboard",
    "StoryboardSegment",
    "Task",
    "User",
]
