import { render, screen } from "@testing-library/react";
import { describe, it, expect, beforeEach } from "vitest";
import App from "./App";
import { useAuthStore } from "@/stores/auth";

describe("App", () => {
  beforeEach(() => {
    useAuthStore.setState({ token: "tok", user: { id: 1, username: "u", email: "e@x", role: "user" } });
  });

  it("登录态渲染项目库占位", () => {
    render(<App />);
    expect(screen.getByText(/工作台/)).toBeInTheDocument();
  });
});
