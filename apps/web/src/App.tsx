import { Navigate, createBrowserRouter } from "react-router";
import { Shell } from "./components/Shell";
import { ApplyPlanScreen } from "./screens/ApplyPlan";
import { AuditScreen } from "./screens/Audit";
import { BlueprintsScreen } from "./screens/Blueprints";
import { DesignerScreen } from "./screens/Designer";
import { InstancesScreen } from "./screens/Instances";
import { NotYetScreen } from "./screens/NotYet";
import { StudioScreen } from "./screens/Studio";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <Shell />,
    children: [
      { index: true, element: <Navigate to="/instances" replace /> },
      { path: "instances", element: <InstancesScreen /> },
      { path: "instances/:id/drift", element: <InstancesScreen /> },
      { path: "blueprints", element: <BlueprintsScreen /> },
      { path: "plans/:id", element: <ApplyPlanScreen /> },
      { path: "designer", element: <DesignerScreen /> },
      { path: "studio", element: <StudioScreen /> },
      { path: "audit", element: <AuditScreen /> },
      { path: "soon/:key", element: <NotYetScreen /> },
      { path: "*", element: <Navigate to="/instances" replace /> },
    ],
  },
]);
