import {
  faCat,
  faHouse,
  faMicrochip,
  faPaw,
  faPlug,
  faPuzzlePiece,
  faScrewdriverWrench,
} from "@fortawesome/free-solid-svg-icons";
import type { IconDefinition } from "@fortawesome/fontawesome-svg-core";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import clsx from "clsx";
import { useEffect } from "react";
import { NavLink, Outlet } from "react-router-dom";

import { useModelHealth } from "../state/modelHealth";
import { useStore } from "../state/store";
import { ModelNotice } from "./models/ModelNotice";
import { PowerOff } from "./PowerOff";

const NAV: { to: string; label: string; icon: IconDefinition; end?: boolean }[] = [
  { to: "/", label: "Home", icon: faHouse, end: true },
  { to: "/skills", label: "Skills", icon: faPuzzlePiece },
  { to: "/tools", label: "Tools", icon: faScrewdriverWrench },
  { to: "/agents", label: "Agents", icon: faPaw },
  { to: "/models", label: "Models", icon: faMicrochip },
  { to: "/integrations", label: "MCP Tools & Integrations", icon: faPlug },
];

const CONNECTION: Record<string, { label: string; dot: string }> = {
  open: { label: "Live", dot: "bg-emerald" },
  connecting: { label: "Connecting…", dot: "bg-warn animate-dot-pulse" },
  down: { label: "Offline — retrying", dot: "bg-danger animate-dot-pulse" },
};

export function Shell() {
  const connection = useStore((s) => s.connection);
  const proposals = useStore((s) => s.proposals.length);
  const conn = CONNECTION[connection] ?? CONNECTION.connecting;
  const modelIssues = useModelHealth((s) => s.report?.issues.length ?? 0);
  const startupCheck = useModelHealth((s) => s.startup);
  useEffect(() => {
    void startupCheck(); // once per session; opens the model notice when a chosen model is gone
  }, [startupCheck]);

  return (
    <div className="flex h-full w-full bg-bg text-ink">
      <nav aria-label="Main" className="flex w-[68px] shrink-0 flex-col items-center gap-1 border-r border-line bg-[#0e1119] py-3">
        <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-cyan/30 to-violet/30 text-lg" title="CatCrewAI">
          <FontAwesomeIcon icon={faCat} className="text-cyan" aria-hidden />
        </div>
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            title={item.label}
            aria-label={item.label}
            className={({ isActive }) =>
              clsx(
                "relative flex h-11 w-11 flex-col items-center justify-center rounded-xl text-[15px] transition",
                isActive ? "bg-cyan/12 text-cyan shadow-[inset_0_0_0_1px_rgba(56,189,248,.35)]" : "text-muted hover:bg-white/5 hover:text-ink",
              )
            }
          >
            <FontAwesomeIcon icon={item.icon} />
            {item.to === "/models" && modelIssues > 0 && (
              <span
                className="absolute right-1 top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-danger px-1 text-[10px] font-bold text-[#2a0a0a]"
                title="A chosen model is unavailable"
                data-testid="models-issue-badge"
              >
                {modelIssues}
              </span>
            )}
            {item.to === "/skills" && proposals > 0 && (
              <span className="absolute right-1 top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-violet px-1 text-[10px] font-bold text-[#150a33]">
                {proposals}
              </span>
            )}
          </NavLink>
        ))}
        <div className="mt-auto flex flex-col items-center gap-2 pb-1">
          <div className="flex flex-col items-center gap-1" title={conn.label} data-testid="connection" data-state={connection}>
            <span className={clsx("h-2 w-2 rounded-full", conn.dot)} />
            <span className="text-[9px] uppercase tracking-wider text-muted">{connection === "open" ? "live" : connection === "down" ? "off" : "…"}</span>
          </div>
          <PowerOff />
        </div>
      </nav>
      <main className="min-w-0 flex-1 overflow-hidden">
        <Outlet />
      </main>
      <ModelNotice />
    </div>
  );
}
