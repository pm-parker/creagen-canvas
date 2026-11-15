/** 后端 viewport JSON（{"x","y","k"}）↔ React Flow Viewport（{x,y,zoom}）互转。 */

export interface BackendViewport {
  x: number;
  y: number;
  k: number;
}

export interface FlowViewport {
  x: number;
  y: number;
  zoom: number;
}

export const DEFAULT_BACKEND_VIEWPORT: BackendViewport = { x: 0, y: 0, k: 1 };

function _isViewportLike(v: unknown): v is Partial<BackendViewport> {
  return typeof v === "object" && v !== null;
}

export function toFlowViewport(backend: unknown): FlowViewport {
  if (!_isViewportLike(backend)) return { x: 0, y: 0, zoom: 1 };
  const { x = 0, y = 0, k = 1 } = backend;
  return { x, y, zoom: k };
}

export function toBackendViewport(flow: FlowViewport): BackendViewport {
  return { x: flow.x, y: flow.y, k: flow.zoom };
}
