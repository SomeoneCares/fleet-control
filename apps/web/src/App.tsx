import type { ReactNode } from "react";
import { Navigate, createBrowserRouter } from "react-router";
import { Shell } from "./components/Shell";
import { Card, PageHeader } from "./components/ui";
import { useAuth, useMe } from "./lib/auth";
import { AccessScreen } from "./screens/Access";
import { AskFleetScreen } from "./screens/AskFleet";
import { ApplyPlanScreen } from "./screens/ApplyPlan";
import { AuditScreen } from "./screens/Audit";
import { BlueprintsScreen } from "./screens/Blueprints";
import { ContentScreen } from "./screens/Content";
import { DecisionRoomScreen, DecisionRoomsScreen } from "./screens/DecisionRooms";
import { MyDecisionsScreen } from "./screens/MyDecisions";
import { DesignerScreen } from "./screens/Designer";
import { FleetArchitectScreen } from "./screens/FleetArchitect";
import { InstancesScreen } from "./screens/Instances";
import { IntegrationsScreen } from "./screens/Integrations";
import { NotYetScreen } from "./screens/NotYet";
import { OutputsScreen } from "./screens/Outputs";
import { SettingsScreen } from "./screens/Settings";
import { AssuranceScreen } from "./screens/Assurance";
import { TestLabScreen } from "./screens/TestLab";
import { StudioScreen } from "./screens/Studio";
import { WorkspaceHome } from "./screens/Workspace";

function Home() {
  const me = useMe();
  return <Navigate to={me.portal === "workspace" ? "/workspace" : "/instances"} replace />;
}

/** The API refuses what a role may not do; this says so up front instead of showing a broken page. */
function Guard({ permission, children }: { permission: string; children: ReactNode }) {
  const { can, me } = useAuth();
  if (can(permission)) return <>{children}</>;
  return (
    <>
      <PageHeader crumb="Not available" title="Your role cannot open this page" />
      <Card className="p-6 max-w-[640px]">You are signed in as {me?.role_label}. Ask an Admin if you need access.</Card>
    </>
  );
}

const guarded = (permission: string, screen: ReactNode) => <Guard permission={permission}>{screen}</Guard>;

export const router = createBrowserRouter([
  {
    path: "/",
    element: <Shell />,
    children: [
      { index: true, element: <Home /> },
      { path: "workspace", element: <WorkspaceHome /> },
      { path: "instances", element: guarded("instances.read", <InstancesScreen />) },
      { path: "instances/:id/drift", element: guarded("instances.read", <InstancesScreen />) },
      { path: "blueprints", element: guarded("blueprints.read", <BlueprintsScreen />) },
      { path: "plans/:id", element: guarded("plans.read", <ApplyPlanScreen />) },
      { path: "architect", element: guarded("blueprints.read", <FleetArchitectScreen />) },
      { path: "designer", element: guarded("blueprints.read", <DesignerScreen />) },
      { path: "studio", element: guarded("blueprints.read", <StudioScreen />) },
      { path: "audit", element: guarded("audit.read", <AuditScreen />) },
      { path: "access", element: guarded("users.read", <AccessScreen />) },
      { path: "content", element: guarded("content.read", <ContentScreen />) },
      { path: "outputs", element: guarded("content.read", <OutputsScreen />) },
      { path: "rooms", element: guarded("rooms.read", <DecisionRoomsScreen />) },
      { path: "rooms/:id", element: guarded("rooms.read", <DecisionRoomScreen />) },
      { path: "my-decisions", element: guarded("rooms.read", <MyDecisionsScreen />) },
      { path: "ask", element: guarded("ask.use", <AskFleetScreen />) },
      { path: "integrations", element: guarded("instances.read", <IntegrationsScreen />) },
      { path: "testlab", element: guarded("blueprints.read", <TestLabScreen />) },
      { path: "assurance", element: guarded("assurance.read", <AssuranceScreen />) },
      { path: "settings", element: <SettingsScreen /> },  // every role: tabs follow permissions (API tokens for all)
      { path: "soon/:key", element: <NotYetScreen /> },
      { path: "*", element: <Home /> },
    ],
  },
]);
