/** 登录后回跳地址：仅允许同源相对路径（以单个 `/` 开头），
 * 拒绝 `//evil.com`（协议相对 URL）、`/\evil.com` 与绝对 URL，防开放重定向。 */
export function safeNextTarget(next: string | null): string {
  if (!next) return "/";
  if (!next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return "/";
  return next;
}
