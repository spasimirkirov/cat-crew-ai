import { useCallback, useEffect, useState } from "react";

import { McpPanel } from "../components/models/McpPanel";
import { ServiceKeysPanel } from "../components/models/ServiceKeysPanel";
import { PageHeader } from "../components/Ui";
import { api } from "../lib/api";
import type { KeyInfo } from "../lib/types";

/**
 * Outside programs and services the crew can use, kept off the Models page since they are not
 * a chat host or a per-role choice: MCP servers (Blender, Unity, ...) and optional service keys
 * (Brave Search).
 */
export function IntegrationsPage() {
  const [keys, setKeys] = useState<KeyInfo[]>([]);

  // Key details are a nicety: the page works without them.
  const loadKeys = useCallback(() => api.keys.list().then(setKeys).catch(() => undefined), []);
  useEffect(() => {
    void loadKeys();
  }, [loadKeys]);

  return (
    <div className="flex h-full flex-col">
      <PageHeader title="MCP Tools & Integrations" subtitle="Outside programs and services the crew can use, and their optional keys." />
      <div className="min-h-0 flex-1 overflow-y-auto px-8 py-6">
        <div className="grid items-start gap-4 xl:grid-cols-2">
          <McpPanel />
          <ServiceKeysPanel services={keys.filter((k) => k.group === "service")} onKeyChanged={() => void loadKeys()} />
        </div>
      </div>
    </div>
  );
}
