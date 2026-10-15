import { createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { AuthGate } from "@/components/AuthGate";
import { DashboardPage } from "@/pages/DashboardPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { SettingsPage } from "@/pages/SettingsPage";

const rootRoute = createRootRoute({ component: AuthGate, notFoundComponent: NotFoundPage });

const routes = [
  createRoute({ getParentRoute: () => rootRoute, path: "/", component: DashboardPage }),
  createRoute({ getParentRoute: () => rootRoute, path: "/settings", component: SettingsPage }),
];

export const routeTree = rootRoute.addChildren(routes);
export const router = createRouter({ routeTree, defaultPreload: "intent" });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
