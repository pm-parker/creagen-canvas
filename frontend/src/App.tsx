import { useEffect } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, Outlet, useNavigate } from "react-router";
import { RouterProvider } from "react-router/dom";
import { NotFoundPage } from "./pages/NotFoundPage";
import { setUnauthorizedHandler } from "@/lib/api";
import { RequireAuth } from "@/components/RequireAuth";
import { LoginPage } from "@/pages/auth/LoginPage";
import { RegisterPage } from "@/pages/auth/RegisterPage";
import { ProjectsPage } from "@/pages/ProjectsPage";
import { CanvasPage } from "@/pages/canvas/CanvasPage";
import { ChannelsPage } from "@/pages/settings/ChannelsPage";
import { HomePage } from "@/pages/HomePage";
import { QuickGeneratePage } from "@/pages/QuickGeneratePage";
import { PromptsPage } from "@/pages/PromptsPage";
import { TasksPage } from "@/pages/TasksPage";
import { CostsPage } from "@/pages/CostsPage";
import { AssetsPage } from "@/pages/AssetsPage";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
});

/** 401 统一跳登录（登录页自身除外，防循环）。 */
function UnauthorizedBridge() {
  const navigate = useNavigate();
  useEffect(() => {
    setUnauthorizedHandler(() => {
      if (!window.location.pathname.startsWith("/login")) {
        navigate("/login", { replace: true });
      }
    });
    return () => setUnauthorizedHandler(null);
  }, [navigate]);
  return null;
}

const router = createBrowserRouter([
  {
    element: (
      <>
        <UnauthorizedBridge />
        <Outlet />
      </>
    ),
    children: [
      { path: "/login", element: <LoginPage /> },
      { path: "/register", element: <RegisterPage /> },
      {
        element: <RequireAuth />,
        children: [
          { path: "/", element: <HomePage /> },
          { path: "/generate/image", element: <QuickGeneratePage domain="image" /> },
          { path: "/generate/video", element: <QuickGeneratePage domain="video" /> },
          { path: "/prompts", element: <PromptsPage /> },
          { path: "/tasks", element: <TasksPage /> },
          { path: "/projects", element: <ProjectsPage /> },
      { path: "/costs", element: <CostsPage /> },
      { path: "/assets", element: <AssetsPage /> },
          { path: "/canvas/:projectId", element: <CanvasPage /> },
          { path: "/settings/channels", element: <ChannelsPage /> },
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
]);

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
