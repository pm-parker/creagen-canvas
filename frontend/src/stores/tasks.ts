/** 任务运行态 store：节点提交/回流状态机。 */
import { create } from "zustand";

export type TaskRunStatus = "queued" | "generating" | "completed" | "failed";

export interface TaskRun {
  taskId: number;
  status: TaskRunStatus;
  phase?: string;
  outputUrl?: string;
  error?: string;
  sourceNodeId?: string;
  startedAt?: number;
}

export interface GenerationHistoryItem {
  taskId: number;
  outputUrl: string;
  prompt: string;
}

interface TaskStoreState {
  runs: Record<string, TaskRun>; // key: taskId
  /** 节点级生成历史：sourceNodeId → 历次 completed 结果（新在前） */
  generationHistory: Record<string, GenerationHistoryItem[]>;
  attach: (sourceNodeId: string, taskId: number) => void;
  update: (taskId: number, patch: Partial<TaskRun>) => void;
  recordGeneration: (sourceNodeId: string, item: GenerationHistoryItem) => void;
}

export const useTaskStore = create<TaskStoreState>()((set, get) => ({
  runs: {},
  generationHistory: {},

  recordGeneration: (sourceNodeId, item) => {
    const all = get().generationHistory;
    const list = [item, ...(all[sourceNodeId] ?? [])].slice(0, 20);
    set({ generationHistory: { ...all, [sourceNodeId]: list } });
  },

  attach: (sourceNodeId, taskId) =>
    set({
      runs: {
        ...get().runs,
        [taskId]: { taskId, status: "queued", sourceNodeId, startedAt: Date.now() },
      },
    }),
  update: (taskId, patch) => {
    const cur = get().runs[taskId];
    if (!cur) return;
    set({ runs: { ...get().runs, [taskId]: { ...cur, ...patch, taskId } } });
  },
}));
