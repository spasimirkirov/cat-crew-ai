import clsx from "clsx";

import { assetUrl } from "../lib/config";
import type { Agent } from "../lib/types";

interface Props {
  agent: Pick<Agent, "name" | "avatar" | "color">;
  size?: number;
  ring?: boolean;
  className?: string;
}

/** Round agent avatar: the uploaded picture, or a tinted initial. */
export function Avatar({ agent, size = 36, ring = false, className }: Props) {
  const color = agent.color || "#38BDF8";
  const url = assetUrl(agent.avatar);
  return (
    <span
      className={clsx("relative inline-flex shrink-0 items-center justify-center overflow-hidden rounded-full font-semibold", className)}
      style={{
        width: size,
        height: size,
        background: url ? "#0f121a" : `${color}22`,
        color,
        border: `1px solid ${color}${ring ? "cc" : "55"}`,
        fontSize: Math.max(11, Math.round(size * 0.42)),
      }}
      aria-hidden
    >
      {url ? <img src={url} alt="" className="h-full w-full object-cover" draggable={false} /> : (agent.name || "?").slice(0, 1).toUpperCase()}
    </span>
  );
}
