import { useState, type FormEvent } from "react";
import { NavLink, Outlet, useLocation } from "react-router";
import { api } from "../api/client";
import { useAuth, useMe } from "../lib/auth";
import { errorText, useLoad } from "../lib/hooks";
import { initials } from "../lib/view";
import { Banner, Button, Field, INPUT, Icon, Modal, Spinner, type IconName } from "./ui";

export interface NavItem {
  key: string;
  label: string;
  icon: IconName;
  to?: string; // built screens; the rest open a "not built yet" page naming their slice
  slice?: number;
  permission?: string; // hidden from roles without it
}

// Build document §3.1: one shell on every admin screen.
export const NAV: { group: string; items: NavItem[] }[] = [
  { group: "Design", items: [
    { key: "fleet-architect", label: "Fleet Architect", icon: "spark", to: "/architect" },
    { key: "fleet-designer", label: "Fleet Designer", icon: "graph", to: "/designer" },
    { key: "agent-studio", label: "Agent Studio", icon: "bot", to: "/studio" },
    { key: "workflows", label: "Workflows", icon: "flow", slice: 5 },
  ] },
  { group: "Operate", items: [
    { key: "decision-rooms", label: "Decision Rooms", icon: "chat", slice: 4 },
    { key: "assurance", label: "Assurance", icon: "shield", to: "/assurance", permission: "assurance.read" },
    { key: "test-lab", label: "Test Lab", icon: "flask", to: "/testlab" },
  ] },
  { group: "Estate", items: [
    { key: "instances", label: "Instances", icon: "server", to: "/instances" },
    { key: "integrations", label: "Integrations", icon: "plug", to: "/integrations", permission: "instances.read" },
    { key: "messaging", label: "Messaging", icon: "message", slice: 4 },
  ] },
  { group: "Govern", items: [
    { key: "access", label: "Access", icon: "lock", to: "/access", permission: "users.read" },
    { key: "audit-log", label: "Audit log", icon: "file", to: "/audit", permission: "audit.read" },
  ] },
  { group: "Library", items: [
    { key: "blueprints", label: "Blueprints", icon: "layers", to: "/blueprints" },
    { key: "content", label: "Content", icon: "folder", to: "/content", permission: "content.read" },
  ] },
];

// Build document §3.2: Approvers and Viewers get the Workspace, without Design, Estate or Govern.
export const WORKSPACE_NAV: { group: string; items: NavItem[] }[] = [
  { group: "Workspace", items: [
    { key: "workspace", label: "Home", icon: "chat", to: "/workspace" },
    { key: "audit-log", label: "Audit log", icon: "file", to: "/audit", permission: "audit.read" },
  ] },
  { group: "Fleet", items: [
    { key: "fleet-outputs", label: "Fleet outputs", icon: "folder", to: "/outputs", permission: "content.read" },
    { key: "my-decisions", label: "My decisions", icon: "check", slice: 4 },
    { key: "decision-rooms", label: "Decision Rooms", icon: "chat", slice: 4 },
    { key: "ask-the-fleet", label: "Ask the fleet", icon: "spark", slice: 4 },
  ] },
];

export const ALL_NAV_ITEMS: NavItem[] = [...NAV, ...WORKSPACE_NAV].flatMap((g) => g.items);

const ITEM = "h-nav flex items-center gap-2.5 px-2.5 rounded-control text-[13px] no-underline";
const ACTIVE = "bg-primary-tint text-primary font-semibold";
const IDLE = "text-text font-medium hover:bg-container-low";

function SideLink({ item }: { item: NavItem }) {
  const { pathname } = useLocation();
  const to = item.to ?? `/soon/${item.key}`;
  // a plan belongs to the Blueprints library (or, for approvers, to their Workspace home)
  const forced = pathname.startsWith("/plans/") && (item.key === "blueprints" || item.key === "workspace");
  return (
    <NavLink to={to} className={({ isActive }) => `${ITEM} ${isActive || forced ? ACTIVE : IDLE} ${item.to ? "" : "opacity-60"}`}>
      <Icon name={item.icon} />
      {item.label}
    </NavLink>
  );
}

export function Shell() {
  const me = useMe();
  const { can } = useAuth();
  const [menu, setMenu] = useState(false);
  const [changing, setChanging] = useState(false);
  const { signOut } = useAuth();
  const { data: instances } = useLoad(() => (can("instances.read") ? api.instances() : Promise.resolve(null)), [me.role], 15_000);
  const versions = [...new Set((instances ?? []).map((i) => i.hermes_version).filter(Boolean))];
  const groups = (me.portal === "workspace" ? WORKSPACE_NAV : NAV)
    .map((g) => ({ ...g, items: g.items.filter((it) => !it.permission || can(it.permission)) }))
    .filter((g) => g.items.length > 0);

  return (
    <div className="min-h-screen flex flex-col">
      <header className="h-14 shrink-0 flex items-center gap-4 px-5 bg-white border-b border-hairline">
        <div className="flex items-center gap-2.5 w-sidebar">
          <span className="size-8 rounded-control bg-primary text-on-primary flex items-center justify-center"><Icon name="logo" size={18} /></span>
          <div className="leading-tight">
            <div className="text-[14px] font-bold">Fleet Control</div>
            <div className="text-[11px] text-text-secondary truncate max-w-[180px]">
              {me.workspace_name && me.workspace_name !== "Fleet Control" ? me.workspace_name : "for Hermes Agent"}
            </div>
          </div>
        </div>
        <div className="flex-1" />
        <div className="relative">
          <button type="button" onClick={() => setMenu((m) => !m)} aria-haspopup="menu" aria-expanded={menu}
            className="flex items-center gap-2 h-9 pl-1 pr-2.5 rounded-control hover:bg-container-low cursor-pointer text-[13px]">
            <span className="size-7 rounded-full bg-secondary-tint text-secondary text-[11px] font-bold flex items-center justify-center">{initials(me.name || me.email)}</span>
            <span className="text-left leading-tight">
              <span className="block font-medium">{me.name}</span>
              <span className="block text-[11px] text-text-secondary">{me.role_label}</span>
            </span>
          </button>
          {menu && (
            <>
              <div className="fixed inset-0 z-30" onClick={() => setMenu(false)} />
              <div role="menu" className="absolute right-0 top-11 z-40 w-56 bg-white border border-hairline rounded-control py-1 shadow-[0_1px_2px_0_rgba(15,23,42,0.05),0_4px_12px_0_rgba(15,23,42,0.08)]">
                <div className="px-3 py-2 text-small text-text-secondary border-b border-hairline truncate">{me.email}</div>
                <button type="button" role="menuitem" onClick={() => { setMenu(false); setChanging(true); }}
                  className="w-full text-left px-3 py-2 text-[13px] hover:bg-container-low cursor-pointer">Change password</button>
                <button type="button" role="menuitem" onClick={() => void signOut()}
                  className="w-full text-left px-3 py-2 text-[13px] hover:bg-container-low cursor-pointer">Sign out</button>
              </div>
            </>
          )}
        </div>
      </header>
      <div className="flex flex-1 min-h-0">
        <nav className="w-sidebar shrink-0 bg-surface border-r border-hairline px-3 py-4 flex flex-col">
          {groups.map((g) => (
            <div key={g.group} className="mb-4">
              <div className="text-label uppercase text-text-secondary px-2.5 mb-1.5">{g.group}</div>
              {g.items.map((it) => <SideLink key={it.key} item={it} />)}
            </div>
          ))}
          <div className="flex-1" />
          <NavLink to="/settings" className={({ isActive }) => `${ITEM} ${isActive ? ACTIVE : IDLE}`}>
            <Icon name="gear" /> Settings
          </NavLink>
          {instances && (
            <div className="text-small text-text-secondary px-2.5 mt-3">
              {`Connected to ${instances.length} Hermes instance${instances.length === 1 ? "" : "s"}`}
              {versions.length > 0 && <div>Hermes {versions.join(", ")}</div>}
            </div>
          )}
        </nav>
        <main className="flex-1 min-w-0 px-8 py-7">
          <Outlet />
        </main>
      </div>
      {changing && <ChangePasswordModal onClose={() => setChanging(false)} />}
    </div>
  );
}

function ChangePasswordModal({ onClose }: { onClose: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const mismatch = again.length > 0 && next !== again;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.changePassword(current, next);
      setDone(true);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Change password" subtitle="At least 12 characters." width={460} onClose={onClose}
      footer={done ? <Button variant="primary" onClick={onClose}>Done</Button> : <>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" type="submit" form="password-form" disabled={busy || !current || next.length < 12 || next !== again}>{busy && <Spinner />}Change password</Button>
      </>}>
      {done ? <Banner tone="success">Password changed.</Banner> : (
        <form id="password-form" onSubmit={(e) => void submit(e)}>
          <Field label="Current password"><input className={INPUT} type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} autoFocus /></Field>
          <Field label="New password"><input className={INPUT} type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} /></Field>
          <Field label="New password again" hint={mismatch ? "The two passwords are different." : undefined}>
            <input className={INPUT} type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} />
          </Field>
          {error && <Banner tone="error">{error}</Banner>}
        </form>
      )}
    </Modal>
  );
}
