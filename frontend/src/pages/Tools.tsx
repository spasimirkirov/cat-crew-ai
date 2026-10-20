import { faCircleCheck, faDownload, faPlus, faScrewdriverWrench, faXmark } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useMemo, useState } from "react";

import { AssignmentMatrix, type MatrixRow } from "../components/matrix/AssignmentMatrix";
import { ImportUrlModal } from "../components/matrix/ImportUrlModal";
import { RegisterToolModal } from "../components/matrix/RegisterToolModal";
import { SearchBox } from "../components/matrix/SearchBox";
import { ToolDrawer } from "../components/matrix/ToolDrawer";
import { useAssignmentMatrix, type MatrixApi } from "../components/matrix/useAssignmentMatrix";
import { useMatrixAgents } from "../components/matrix/useMatrixAgents";
import { EmptyState, ErrorNote, PageHeader, Spinner } from "../components/Ui";
import { api } from "../lib/api";
import type { Tool } from "../lib/types";
import { useIsAdmin } from "../state/auth";

// Closures (not `api.tools` itself) so every call resolves the method at call time.
const toolsApi: MatrixApi<Tool> = {
  list: () => api.tools.list(),
};

type ModalKind = "register" | "import" | null;

export function ToolsPage() {
  const agents = useMatrixAgents();
  const admin = useIsAdmin(); // registering a tool is a global setting
  const { items, loading, loadError, reload } = useAssignmentMatrix(toolsApi);

  const [query, setQuery] = useState("");
  const [modal, setModal] = useState<ModalKind>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return items.filter((t) => !q || t.name.toLowerCase().includes(q) || t.description.toLowerCase().includes(q) || t.handler.toLowerCase().includes(q));
  }, [items, query]);

  const rows = useMemo<MatrixRow[]>(
    () => visible.map((t) => ({ id: t.id, name: t.name, description: t.description, is_global: t.is_global, agent_ids: t.agent_ids, locked_agent_ids: t.locked_agent_ids, mono: t.handler })),
    [visible],
  );

  const opened = openId ? items.find((t) => t.id === openId) : undefined;

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Tools"
        subtitle="Python functions each cat ships with. A tool you register goes to every cat."
        actions={
          admin && (
          <>
            <button className="btn" onClick={() => setModal("import")}>
              <FontAwesomeIcon icon={faDownload} />
              Import from URL
            </button>
            <button className="btn btn-primary" onClick={() => setModal("register")}>
              <FontAwesomeIcon icon={faPlus} />
              Register tool
            </button>
          </>
          )
        }
      />

      <div className="flex min-h-0 flex-1 flex-col gap-3 px-8 pb-6 pt-4">
        {notice && (
          <div role="status" className="flex items-center gap-2 rounded-lg border border-emerald/30 bg-emerald/10 px-3 py-2 text-sm text-emerald">
            <FontAwesomeIcon icon={faCircleCheck} />
            <span className="min-w-0 flex-1">{notice}</span>
            <button className="btn btn-ghost btn-sm" onClick={() => setNotice(null)} aria-label="Dismiss message">
              <FontAwesomeIcon icon={faXmark} />
            </button>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-3">
          <SearchBox value={query} onChange={setQuery} placeholder="Search tools" label="Search tools" className="w-72" />
          <span className="ml-auto text-xs text-muted" aria-live="polite">
            {loading ? "" : `Showing ${visible.length} of ${items.length} tools`}
          </span>
        </div>

        <div className="card min-h-0 flex-1 overflow-auto">
          {loading ? (
            <div className="flex h-full items-center justify-center gap-2 py-16 text-sm text-muted">
              <Spinner /> Loading tools
            </div>
          ) : loadError ? (
            <div className="space-y-3 p-6">
              <ErrorNote message={loadError} />
              <button className="btn btn-sm" onClick={() => void reload()}>
                Try again
              </button>
            </div>
          ) : items.length === 0 ? (
            <EmptyState
              icon={faScrewdriverWrench}
              title="No tools registered"
              hint="Register a function from backend/tool_functions so the cats can call it."
              action={
                <button className="btn btn-primary btn-sm mt-2" onClick={() => setModal("register")}>
                  <FontAwesomeIcon icon={faPlus} />
                  Register tool
                </button>
              }
            />
          ) : visible.length === 0 ? (
            <EmptyState icon={faScrewdriverWrench} title="No tools match" hint="Try another search." />
          ) : (
            <AssignmentMatrix
              rows={rows}
              agents={agents}
              columnLabel="Tool"
              selectedId={openId}
              onOpen={setOpenId}
            />
          )}
        </div>
      </div>

      {opened && (
        <ToolDrawer
          tool={opened}
          agents={agents}
          onClose={() => setOpenId(null)}
          onDeleted={() => {
            setOpenId(null);
            setNotice(`Deleted ${opened.name}.`);
            void reload();
          }}
        />
      )}
      {modal === "register" && (
        <RegisterToolModal
          onClose={() => setModal(null)}
          onRegistered={(tool) => {
            setNotice(`Registered ${tool.name}.`);
            void reload();
          }}
        />
      )}
      {modal === "import" && (
        <ImportUrlModal
          title="Import tool from URL"
          hint="Paste a link to a tool definition. The backend validates it before registering it."
          placeholder="https://example.com/tools/my_tool.py"
          onImport={async (url) => (await api.tools.importFromUrl(url)).message}
          onDone={(message) => {
            setModal(null);
            setNotice(message);
            void reload();
          }}
          onClose={() => setModal(null)}
        />
      )}
    </div>
  );
}
