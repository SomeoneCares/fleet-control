import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router";
import { router } from "./App";
import { AuthProvider, useAuth } from "./lib/auth";
import { SignInScreen } from "./screens/SignIn";
import "./styles/app.css";

/** Nothing but Sign in until there is a session; a 401 later brings Sign in back. */
function Gate() {
  const { me } = useAuth();
  if (me === undefined) return <div className="min-h-screen flex items-center justify-center text-text-secondary">Loading…</div>;
  if (me === null) return <SignInScreen />;
  return <RouterProvider router={router} />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AuthProvider>
      <Gate />
    </AuthProvider>
  </StrictMode>,
);
