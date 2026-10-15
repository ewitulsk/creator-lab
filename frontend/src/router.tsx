import { createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { AuthGate } from "@/components/AuthGate";
import { ChannelDetailPage } from "@/pages/ChannelDetailPage";
import { ChannelsPage } from "@/pages/ChannelsPage";
import { DashboardPage } from "@/pages/DashboardPage";
import { DiscoverChannelPage } from "@/pages/DiscoverChannelPage";
import { DiscoverPage } from "@/pages/DiscoverPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { SettingsPage } from "@/pages/SettingsPage";
import { VideoDetailPage } from "@/pages/VideoDetailPage";

const rootRoute = createRootRoute({ component: AuthGate, notFoundComponent: NotFoundPage });
const getParentRoute = () => rootRoute;

export const routeTree = rootRoute.addChildren([
  createRoute({ getParentRoute, path: "/", component: DashboardPage }),
  createRoute({ getParentRoute, path: "/discover", component: DiscoverPage }),
  createRoute({ getParentRoute, path: "/discover/$channelId", component: DiscoverChannelPage }),
  createRoute({ getParentRoute, path: "/channels", component: ChannelsPage }),
  createRoute({ getParentRoute, path: "/channels/$channelId", component: ChannelDetailPage }),
  createRoute({ getParentRoute, path: "/videos/$videoId", component: VideoDetailPage }),
  createRoute({ getParentRoute, path: "/settings", component: SettingsPage }),
]);
export const router = createRouter({ routeTree, defaultPreload: "intent" });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
