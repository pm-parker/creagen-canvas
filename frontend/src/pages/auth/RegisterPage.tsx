import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { apiFetch } from "@/lib/api";
import { useAuthStore } from "@/stores/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function RegisterPage() {
  const navigate = useNavigate();
  const setAuth = useAuthStore((s) => s.setAuth);
  const [form, setForm] = useState({ username: "", email: "", password: "", inviteCode: "" });
  const [error, setError] = useState("");
  const [hint, setHint] = useState("");
  const [busy, setBusy] = useState(false);

  function update(key: keyof typeof form) {
    return (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [key]: e.target.value });
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setHint("");
    try {
      const { access_token } = await apiFetch<{ access_token: string }>("/api/auth/register", {
        method: "POST",
        json: {
          username: form.username,
          email: form.email,
          password: form.password,
          invite_code: form.inviteCode || null,
        },
      });
      // 先落 token 再拉用户信息：me 请求需要 Authorization 头
      setAuth(access_token, null);
      const me = await apiFetch<{ id: number; username: string; email: string; role: string }>(
        "/api/auth/me",
      );
      setAuth(access_token, me);
      navigate("/");
    } catch (err) {
      const e = err as { message?: string; hint?: string };
      setError(e.message ?? "注册失败");
      setHint(e.hint ?? "");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-50">
      <form onSubmit={handleSubmit} className="w-full max-w-sm space-y-4 rounded-2xl bg-white p-8 shadow-sm">
        <h1 className="text-xl font-semibold">注册 CreaGen Canvas</h1>
        <div className="space-y-1.5">
          <Label htmlFor="username">用户名</Label>
          <Input id="username" value={form.username} onChange={update("username")} required />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="email">邮箱</Label>
          <Input id="email" type="email" value={form.email} onChange={update("email")} required />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="password">密码</Label>
          <Input id="password" type="password" value={form.password} onChange={update("password")} required />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="inviteCode">邀请码</Label>
          <Input id="inviteCode" value={form.inviteCode} onChange={update("inviteCode")} />
        </div>
        {error && (
          <div className="text-sm text-red-600">
            <p>{error}</p>
            {hint && <p className="mt-1 text-xs text-zinc-500">{hint}</p>}
          </div>
        )}
        <Button type="submit" className="w-full" disabled={busy}>
          {busy ? "注册中…" : "注册"}
        </Button>
        <p className="text-center text-sm text-zinc-500">
          已有账号？{" "}
          <Link to="/login" className="text-zinc-900 underline">
            登录
          </Link>
        </p>
      </form>
    </div>
  );
}
