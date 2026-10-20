import { useEffect, type ReactNode } from "react";
import { HashRouter, Route, Routes } from "react-router-dom";

import { AuthGate } from "./components/AuthGate";
import { ContextMenu } from "./components/ContextMenu";
import { Shell } from "./components/Shell";
import { BootGate } from "./components/StartupScreen";
import { AgentsPage } from "./pages/Agents";
import { HomePage } from "./pages/Home";
import { IntegrationsPage } from "./pages/Integrations";
import { ModelsPage } from "./pages/Models";
import { ProjectRoomPage } from "./pages/ProjectRoom";
import { ProjectSettingsPage } from "./pages/ProjectSettings";
import { SkillsPage } from "./pages/Skills";
import { ToolsPage } from "./pages/Tools";
import { UsersPage } from "./pages/Users";
import { startLive, stopLive } from "./state/live";

/** The app-wide event socket, open while this is mounted (after sign-in, see AuthGate). */
function Live({ children }: { children: ReactNode }) {
  useEffect(() => {
    startLive();
    return () => stopLive();
  }, []);
  return <>{children}</>;
}

export default function App() {
  // HashRouter: works from the Tauri asset protocol and from `vite preview` alike.
  return (
    <>
    <ContextMenu />
    <AuthGate>
    <Live>
    <BootGate>
      <HashRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes>
          <Route element={<Shell />}>
            <Route index element={<HomePage />} />
            <Route path="project/:pid" element={<ProjectRoomPage />} />
            <Route path="project/:pid/settings" element={<ProjectSettingsPage />} />
            <Route path="project/:pid/:botId" element={<ProjectRoomPage />} />
            <Route path="skills" element={<SkillsPage />} />
            <Route path="tools" element={<ToolsPage />} />
            <Route path="agents" element={<AgentsPage />} />
            <Route path="models" element={<ModelsPage />} />
            <Route path="integrations" element={<IntegrationsPage />} />
            <Route path="users" element={<UsersPage />} />
            <Route path="*" element={<HomePage />} />
          </Route>
        </Routes>
      </HashRouter>
    </BootGate>
    </Live>
    </AuthGate>
    </>
  );
}
