import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Clapperboard, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  fetchCharacterLibrary,
  saveCharacterToLibrary,
  deleteCharacter,
} from "@/lib/storyboardApi";
import { type CharacterAsset } from "@/lib/storyboard";

export function CharactersPage() {
  const queryClient = useQueryClient();
  const libQuery = useQuery({ queryKey: ["characters-lib"], queryFn: fetchCharacterLibrary });
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  const create = useMutation({
    mutationFn: () => saveCharacterToLibrary(name.trim(), description.trim()),
    onSuccess: () => {
      setName("");
      setDescription("");
      void queryClient.invalidateQueries({ queryKey: ["characters-lib"] });
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) => deleteCharacter(id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["characters-lib"] }),
  });

  const characters = libQuery.data?.characters ?? [];

  return (
    <div className="min-h-screen bg-zinc-50 dark:bg-zinc-950">
      <header className="flex items-center justify-between border-b border-zinc-200 bg-white px-6 py-4 dark:border-zinc-800 dark:bg-zinc-900">
        <h1 className="flex items-center gap-2 text-lg font-semibold dark:text-zinc-100">
          <Clapperboard className="h-5 w-5 text-amber-500" /> 角色库
        </h1>
      </header>
      <main className="mx-auto max-w-3xl space-y-4 p-6">
        <div className="rounded-xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <div>
              <Label htmlFor="char-name">角色名称</Label>
              <input
                id="char-name"
                aria-label="角色名称"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="如：阿珍"
                className="mt-1 w-full rounded-lg border border-zinc-200 px-2.5 py-1.5 text-sm outline-none focus:border-zinc-400 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
              />
            </div>
            <div>
              <Label htmlFor="char-desc">外观描述（英文锚定）</Label>
              <input
                id="char-desc"
                aria-label="外观描述"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="40yo woman, flour-dusted apron"
                className="mt-1 w-full rounded-lg border border-zinc-200 px-2.5 py-1.5 text-sm outline-none focus:border-zinc-400 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
              />
            </div>
          </div>
          <Button
            className="mt-2"
            size="sm"
            disabled={!name.trim() || !description.trim() || create.isPending}
            onClick={() => create.mutate()}
          >
            <Plus className="h-3.5 w-3.5" /> 添加角色
          </Button>
        </div>
        <div className="space-y-2">
          {characters.map((c: CharacterAsset) => (
            <div
              key={c.id}
              data-testid={`character-${c.id}`}
              className="flex items-center gap-3 rounded-xl border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900"
            >
              {c.ref_image_url ? (
                <img
                  aria-label={`${c.name} 定妆照`}
                  src={c.ref_image_url}
                  alt={c.name}
                  className="h-12 w-12 rounded-lg border border-zinc-200 object-cover dark:border-zinc-700"
                />
              ) : (
                <div
                  aria-label={`${c.name} 定妆照`}
                  aria-hidden="true"
                  className="flex h-12 w-12 items-center justify-center rounded-lg bg-zinc-100 text-xs text-zinc-400 dark:bg-zinc-800"
                >
                  无图
                </div>
              )}
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-zinc-900 dark:text-zinc-100">{c.name}</p>
                <p className="truncate text-xs text-zinc-500">{c.description}</p>
              </div>
              <button
                aria-label={`删除角色 ${c.name}`}
                onClick={() => remove.mutate(c.id)}
                className="inline-flex min-h-6 min-w-6 cursor-pointer items-center justify-center text-zinc-300 hover:text-red-500"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          ))}
          {characters.length === 0 && !libQuery.isLoading && (
            <div className="rounded-xl border border-dashed border-zinc-300 py-10 text-center text-sm text-zinc-400 dark:border-zinc-700">
              还没有角色——添加一个，跨片复用
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
