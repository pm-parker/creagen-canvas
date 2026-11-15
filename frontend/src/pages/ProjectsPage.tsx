import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { useProjectsQuery, useProjectMutations } from "@/hooks/useProjects";
import { useAuthStore } from "@/stores/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Plus, Trash2, Pencil } from "lucide-react";

function ProjectCard({ id, title }: { id: number; title: string }) {
  const { remove, rename } = useProjectMutations();
  const [confirming, setConfirming] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(title);

  function commitRename() {
    setEditing(false);
    if (draft.trim() && draft !== title) {
      rename.mutate({ id, title: draft.trim() });
    }
  }

  return (
    <div
      data-testid={`project-card-${id}`}
      className="group rounded-xl border border-zinc-200 bg-white p-4 shadow-xs transition-shadow hover:shadow-md"
    >
      <Link to={`/canvas/${id}`} className="block">
        {editing ? (
          <Input
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commitRename}
            onKeyDown={(e) => e.key === "Enter" && commitRename()}
            onClick={(e) => e.preventDefault()}
          />
        ) : (
          <h3 className="truncate text-base font-medium text-zinc-900">{title}</h3>
        )}
      </Link>
      <div className="mt-3 flex items-center gap-1">
        <Button variant="ghost" size="icon" aria-label="重命名" onClick={() => setEditing(true)}>
          <Pencil className="h-4 w-4" />
        </Button>
        {confirming ? (
          <Button
            variant="destructive"
            size="sm"
            onClick={() => {
              remove.mutate(id);
              setConfirming(false);
            }}
          >
            确认删除
          </Button>
        ) : (
          <Button variant="ghost" size="icon" aria-label="删除" onClick={() => setConfirming(true)}>
            <Trash2 className="h-4 w-4 text-zinc-500" />
          </Button>
        )}
      </div>
    </div>
  );
}

export function ProjectsPage() {
  const query = useProjectsQuery();
  const navigate = useNavigate();
  const { create } = useProjectMutations();
  const projects = query.data?.projects;
  const isLoading = query.isLoading;
  const user = useAuthStore((s) => s.user);

  return (
    <div className="min-h-screen bg-zinc-50">
      <header className="flex items-center justify-between border-b border-zinc-200 bg-white px-6 py-4">
        <h1 className="text-lg font-semibold">画布库</h1>
        <div className="flex items-center gap-3">
          <span className="text-sm text-zinc-500">{user?.username}</span>
          <Button
            onClick={() =>
              create.mutate(undefined, {
                onSuccess: (res) => navigate(`/canvas/${res.project.id}`),
              })
            }
            disabled={create.isPending}
          >
            <Plus className="h-4 w-4" /> 新建画布
          </Button>
        </div>
      </header>
      <main className="mx-auto max-w-6xl p-6">
        {isLoading ? (
          <p className="text-sm text-zinc-500">加载中…</p>
        ) : projects && projects.length > 0 ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {projects.map((p) => (
              <ProjectCard key={p.id} id={p.id} title={p.title} />
            ))}
          </div>
        ) : (
          <p className="text-sm text-zinc-500">还没有画布，点右上角「新建画布」开始创作。</p>
        )}
      </main>
    </div>
  );
}
