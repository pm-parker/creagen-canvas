import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { apiFetch } from "@/lib/api";
import { useAuthStore } from "@/stores/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface TokenResponse {
  access_token: string;
}

export function LoginPage() {
  const navigate = useNavigate();
  const setAuth = useAuthStore((s) => s.setAuth);
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const { access_token } = await apiFetch<TokenResponse>("/api/auth/login", {
        method: "POST",
        json: { identifier, password },
      });
      // 先落 token 再拉用户信息：me 请求需要 Authorization 头
      setAuth(access_token, null);
      const me = await apiFetch<{ id: number; username: string; email: string; role: string }>(
        "/api/auth/me",
      );
      setAuth(access_token, me);
      navigate("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-50">
      <form onSubmit={handleSubmit} className="w-full max-w-sm space-y-4 rounded-2xl bg-white p-8 shadow-sm">
        <h1 className="text-xl font-semibold">登录 CreaGen Canvas</h1>
        <div className="space-y-1.5">
          <Label htmlFor="identifier">用户名或邮箱</Label>
          <Input id="identifier" value={identifier} onChange={(e) => setIdentifier(e.target.value)} required />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="password">密码</Label>
          <Input id="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <Button type="submit" className="w-full" disabled={busy}>
          {busy ? "登录中…" : "登录"}
        </Button>
        <p className="text-center text-sm text-zinc-500">
          没有账号？{" "}
          <Link to="/register" className="text-zinc-900 underline">
            注册
          </Link>
        </p>
      </form>
    </div>
  );
}
