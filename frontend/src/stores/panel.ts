import { create } from "zustand";

interface PanelState {
  activeNodeId: string | null;
  /** 智能引用：被排除的上游节点（按 activeNodeId 分组） */
  excludedRefs: Record<string, string[]>;
  toggleRef: (activeNodeId: string, upstreamId: string) => void;
  openFor: (nodeId: string) => void;
  close: () => void;
}

export const usePanel = create<PanelState>()((set, get) => ({
  activeNodeId: null,
  excludedRefs: {},
  toggleRef: (activeNodeId, upstreamId) => {
    const cur = get().excludedRefs[activeNodeId] ?? [];
    const next = cur.includes(upstreamId)
      ? cur.filter((x) => x !== upstreamId)
      : [...cur, upstreamId];
    set({ excludedRefs: { ...get().excludedRefs, [activeNodeId]: next } });
  },
  openFor: (nodeId) => set({ activeNodeId: nodeId }),
  close: () => set({ activeNodeId: null }),
}));
