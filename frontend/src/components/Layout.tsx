import { Link, Outlet } from "@tanstack/react-router";
import { Activity, BarChart3, Columns3, Compass, FlaskConical, Gauge, Grid3x3, LogOut, type LucideIcon, Settings, Smartphone, Tv } from "lucide-react";
import { useLogout, useMe } from "@/api/queries";
import { Button } from "@/components/ui/button";

type NavItem = { to: string; label: string; icon: LucideIcon; exact?: boolean };

export const NAV: NavItem[] = [
  { to: "/", label: "Dashboard", icon: Gauge, exact: true },
  { to: "/discover", label: "Discover", icon: Compass },
  { to: "/channels", label: "Channels", icon: Tv },
  { to: "/compare", label: "Compare", icon: Columns3 },
  { to: "/shorts", label: "Shorts", icon: Smartphone },
  { to: "/coverage", label: "Coverage", icon: Grid3x3 },
  { to: "/jobs", label: "Jobs", icon: Activity },
  { to: "/studies", label: "Studies", icon: FlaskConical },
  { to: "/settings", label: "Settings", icon: Settings },
];

export function Layout({ nav = NAV }: { nav?: NavItem[] }) {
  const me = useMe();
  const logout = useLogout();
  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-52 shrink-0 flex-col border-r bg-card/50 p-3 md:flex">
        <Link to="/" className="mb-6 flex items-center gap-2 px-2 pt-1 font-semibold">
          <BarChart3 className="size-5 text-primary" />
          creator-lab
        </Link>
        <nav className="flex flex-1 flex-col gap-0.5">
          {nav.map((item) => (
            <Link
              key={item.to}
              to={item.to}
              activeOptions={{ exact: item.exact ?? false }}
              className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground data-[status=active]:bg-accent data-[status=active]:font-medium data-[status=active]:text-foreground"
            >
              <item.icon className="size-4" />
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="flex items-center justify-between border-t pt-3 text-sm">
          <span className="truncate px-2 text-muted-foreground">{me.data?.user.username}</span>
          <Button variant="ghost" size="icon" title="Sign out" onClick={() => logout.mutate()}>
            <LogOut />
          </Button>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-1 overflow-x-auto border-b p-2 md:hidden">
          {nav.map((item) => (
            <Link
              key={item.to}
              to={item.to}
              activeOptions={{ exact: item.exact ?? false }}
              className="rounded-md px-2 py-1 text-sm text-muted-foreground data-[status=active]:bg-accent data-[status=active]:text-foreground"
            >
              {item.label}
            </Link>
          ))}
        </header>
        <main className="mx-auto w-full max-w-[1400px] flex-1 p-4 md:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
