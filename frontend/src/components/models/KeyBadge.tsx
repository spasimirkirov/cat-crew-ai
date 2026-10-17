import clsx from "clsx";

import type { JevKeySource } from "../../lib/types";

const TEXT: Record<JevKeySource, string> = {
  saved: "Key saved",
  environment: "Env key",
  openrouter: "Uses OpenRouter key",
  none: "No key",
};

/** Whether a key is set, and where it comes from. `none` is a warning only when the thing needs one.
 * `source` is typed as the wider `JevKeySource` (adds `"openrouter"`, for Jev's badge) -- every other
 * caller only ever passes a plain `KeySource`, which is a subset and still type-checks fine here. */
export function KeyBadge({ source, required }: { source: JevKeySource; required: boolean }) {
  if (source === "none" && !required) return null;
  return (
    <span
      title={source === "environment" ? "The key comes from an environment variable" : undefined}
      className={clsx("chip !py-0", source === "none" ? "border-warn/40 bg-warn/10 text-warn" : "border-emerald/40 bg-emerald/10 text-emerald")}>
      {TEXT[source]}
    </span>
  );
}
