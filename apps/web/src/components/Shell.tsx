import { NavLink, Outlet, useLocation } from "react-router";
import { api } from "../api/client";
import { useLoad } from "../lib/hooks";
import { Icon, type IconName } from "./ui";

export interface NavItem {
  key: string;
  label: string;
  icon: IconName;
  to?: string; // built screens; the rest open a "not built yet" page naming their slice
  slice?: number;
}

// Build document §3.1: one shell on every admin screen.
export const NAV: { group: string; items: NavItem[] }[] = [
  { group: "Design", items: [
    { key: "fleet-architect", label: "Fleet Architect", icon: "spark", slice: 2 },
    { key: "fleet-designer", label: "Fleet Designer", icon: "graph", slice: 1 },
    { key: "agent-studio", label: "Agent Studio", icon: "bot", slice: 1 },
    { key: "workflows", label: "Workflows", icon: "flow", slice: 5 },
  ] },
  { group: "Operate", items: [
    { key: "decision-rooms", label: "Decision Rooms", icon: "chat", slice: 4 },
    { key: "assurance", label: "Assurance", icon: "shield", slice: 3 },
    { key: "test-lab", label: "Test Lab", icon: "flask", slice: 3 },
  ] },
  { group: "Estate", items: [
    { key: "instances", label: "Instances", icon: "server", to: "/instances" },
    { key: "integrations", label: "Integrations", icon: "plug", slice: 3 },
    { key: "messaging", label: "Messaging", icon: "message", slice: 4 },
  ] },
  { group: "Govern", items: [
    { key: "access", label: "Access", icon: "lock", slice: 1 },
    { key: "audit-log", label: "Audit log", icon: "file", slice: 1 },
  ] },
  { group: "Library", items: [
    { key: "blueprints", label: "Blueprints", icon: "layers", to: "/blueprints" },
    { key: "content", label: "Content", icon: "folder", slice: 4 },
  ] },
];

const ITEM = "h-nav flex items-center gap-2.5 px-2.5 rounded-control text-[13px] no-underline";
const ACTIVE = "bg-primary-tint text-primary font-semibold";
const IDLE = "text-text font-medium hover:bg-container-low";

function SideLink({ item }: { item: NavItem }) {
  const { pathname } = useLocation();
  const to = item.to ?? `/soon/${item.key}`;
  // a plan belongs to the Blueprints library
  const forced = item.key === "blueprints" && pathname.startsWith("/plans/");
  return (
    <NavLink to={to} className={({ isActive }) => `${ITEM} ${isActive || forced ? ACTIVE : IDLE} ${item.to ? "" : "opacity-60"}`}>
      <Icon name={item.icon} />
      {item.label}
    </NavLink>
  );
}

export function Shell() {
  const { data: instances } = useLoad(api.instances, [], 15_000);
  const versions = [...new Set((instances ?? []).map((i) => i.hermes_version).filter(Boolean))];
  return (
    <div className="min-h-screen flex flex-col">
      <header className="h-14 shrink-0 flex items-center gap-4 px-5 bg-white border-b border-hairline">
        <div className="flex items-center gap-2.5 w-sidebar">
          <span className="size-8 rounded-control bg-primary text-on-primary flex items-center justify-center"><Icon name="logo" size={18} /></span>
          <div className="leading-tight">
            <div className="text-[14px] font-bold">Fleet Control</div>
            <div className="text-[11px] text-text-secondary">for Hermes Agent</div>
          </div>
        </div>
        <div className="flex-1" />
        <div className="flex items-center gap-2 text-[13px]" title="Sign-in arrives later in Slice 1; the API trusts a placeholder user for now.">
          <span className="size-7 rounded-full bg-secondary-tint text-secondary text-[11px] font-bold flex items-center justify-center">DV</span>
          <span className="font-medium">dev@local</span>
        </div>
      </header>
      <div className="flex flex-1 min-h-0">
        <nav className="w-sidebar shrink-0 bg-surface border-r border-hairline px-3 py-4 flex flex-col">
          {NAV.map((g) => (
            <div key={g.group} className="mb-4">
              <div className="text-label uppercase text-text-secondary px-2.5 mb-1.5">{g.group}</div>
              {g.items.map((it) => <SideLink key={it.key} item={it} />)}
            </div>
          ))}
          <div className="flex-1" />
          <NavLink to="/soon/settings" className={({ isActive }) => `${ITEM} ${isActive ? ACTIVE : IDLE} opacity-60`}>
            <Icon name="gear" /> Settings
          </NavLink>
          <div className="text-small text-text-secondary px-2.5 mt-3">
            {instances === null ? "Connecting to the API…" : `Connected to ${instances.length} Hermes instance${instances.length === 1 ? "" : "s"}`}
            {versions.length > 0 && <div>Hermes {versions.join(", ")}</div>}
          </div>
        </nav>
        <main className="flex-1 min-w-0 px-8 py-7">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
