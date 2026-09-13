import { Navigate, createBrowserRouter } from "react-router";
import { Shell } from "./components/Shell";
import { ApplyPlanScreen } from "./screens/ApplyPlan";
import { BlueprintsScreen } from "./screens/Blueprints";
import { InstancesScreen } from "./screens/Instances";
import { NotYetScreen } from "./screens/NotYet";

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
      { path: "soon/:key", element: <NotYetScreen /> },
      { path: "*", element: <Navigate to="/instances" replace /> },
    ],
  },
]);
