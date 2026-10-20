import { faCircleCheck, faDownload, faPlus, faPuzzlePiece, faXmark } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import clsx from "clsx";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AssignmentMatrix, type MatrixRow } from "../components/matrix/AssignmentMatrix";
import { ImportUrlModal } from "../components/matrix/ImportUrlModal";
import { NewSkillModal } from "../components/matrix/NewSkillModal";
import { ProposalsPanel } from "../components/matrix/ProposalsPanel";
import { SearchBox } from "../components/matrix/SearchBox";
import { SkillDrawer } from "../components/matrix/SkillDrawer";
import { TypeChip } from "../components/matrix/TypeChip";
import { useAssignmentMatrix, type MatrixApi } from "../components/matrix/useAssignmentMatrix";
import { useMatrixAgents } from "../components/matrix/useMatrixAgents";
import { EmptyState, ErrorNote, PageHeader, Spinner } from "../components/Ui";
import { api } from "../lib/api";
import type { Skill, SkillType } from "../lib/types";
import { useStore } from "../state/store";
import { useIsAdmin } from "../state/auth";

// Closures (not `api.skills` itself) so every call resolves the method at call time.
const skillsApi: MatrixApi<Skill> = {
  list: () => api.skills.list(),
};

type Filter = "all" | SkillType;
const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "bundled", label: "Bundled" },
  { id: "custom", label: "Custom" },
  { id: "marketplace", label: "Marketplace" },
  { id: "auto_generated", label: "Auto-generated" },
];

type ModalKind = "new" | "import" | null;

export function SkillsPage() {
  const agents = useMatrixAgents();
  const admin = useIsAdmin(); // adding or importing a playbook is a global setting
  const { items, loading, loadError, reload } = useAssignmentMatrix(skillsApi);

  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [modal, setModal] = useState<ModalKind>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // -- Hermes proposals (refetched whenever a `skill.proposed` event lands in the store)
  const storeProposals = useStore((s) => s.proposals);
  const [proposals, setProposals] = useState<Skill[]>([]);
  useEffect(() => {
    let cancelled = false;
    api.skills
      .proposals()
      .then((list) => !cancelled && setProposals(list))
      .catch(() => {
        /* proposals are optional */
      });
    return () => {
      cancelled = true;
    };
  }, [storeProposals]);

  const approve = useCallback(
    async (skillId: string, agentId: string) => {
      await api.skills.approve(skillId, agentId);
      setProposals((list) => list.filter((p) => p.id !== skillId));
      void useStore.getState().refreshProposals();
      void reload();
    },
    [reload],
  );
  const dismiss = useCallback(async (skillId: string) => {
    await api.skills.dismiss(skillId);
    setProposals((list) => list.filter((p) => p.id !== skillId));
    void useStore.getState().refreshProposals();
  }, []);

  // -- filtering
  const counts = useMemo(() => {
    const c: Record<string, number> = { all: items.length };
    for (const s of items) c[s.type] = (c[s.type] ?? 0) + 1;
    return c;
  }, [items]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return items.filter(
      (s) => (filter === "all" || s.type === filter) && (!q || s.name.toLowerCase().includes(q) || s.description.toLowerCase().includes(q)),
    );
  }, [items, query, filter]);

  const rows = useMemo<MatrixRow[]>(
    () => visible.map((s) => ({ id: s.id, name: s.name, description: s.description, is_global: s.is_global, agent_ids: s.agent_ids, locked_agent_ids: s.locked_agent_ids, badge: <TypeChip type={s.type} /> })),
    [visible],
  );

  const opened = openId ? items.find((s) => s.id === openId) : undefined;

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Skills"
        subtitle="Playbooks each cat ships with. A skill you add goes to the cats its target roles name, or to every cat."
        actions={
          admin && (
          <>
            <button className="btn" onClick={() => setModal("import")}>
              <FontAwesomeIcon icon={faDownload} />
              Import from URL
            </button>
            <button className="btn btn-primary" onClick={() => setModal("new")}>
              <FontAwesomeIcon icon={faPlus} />
              New skill
            </button>
          </>
          )
        }
      />

      <div className="flex min-h-0 flex-1 flex-col gap-3 px-8 pb-6 pt-4">
        {admin && <ProposalsPanel proposals={proposals} agents={agents} onApprove={approve} onDismiss={dismiss} />}

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
          <SearchBox value={query} onChange={setQuery} placeholder="Search skills" label="Search skills" className="w-72" />
          <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Filter by type">
            {FILTERS.map((f) => (
              <button
                key={f.id}
                type="button"
                aria-pressed={filter === f.id}
                onClick={() => setFilter(f.id)}
                className={clsx(
                  "chip cursor-pointer px-2.5 py-1 transition hover:border-[#3a405a] hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan/60",
                  filter === f.id && "border-cyan/50 bg-cyan/10 text-cyan hover:text-cyan",
                )}
              >
                {f.label}
                <span className="font-mono opacity-70">{counts[f.id] ?? 0}</span>
              </button>
            ))}
          </div>
          <span className="ml-auto text-xs text-muted" aria-live="polite">
            {loading ? "" : `Showing ${visible.length} of ${items.length} skills`}
          </span>
        </div>

        <div className="card min-h-0 flex-1 overflow-auto">
          {loading ? (
            <div className="flex h-full items-center justify-center gap-2 py-16 text-sm text-muted">
              <Spinner /> Loading skills
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
              icon={faPuzzlePiece}
              title="No skills yet"
              hint="Write a playbook or import a SKILL.md from a URL and the cats can start using it."
              action={
                <button className="btn btn-primary btn-sm mt-2" onClick={() => setModal("new")}>
                  <FontAwesomeIcon icon={faPlus} />
                  New skill
                </button>
              }
            />
          ) : visible.length === 0 ? (
            <EmptyState icon={faPuzzlePiece} title="No skills match" hint="Try another search or filter." />
          ) : (
            <AssignmentMatrix
              rows={rows}
              agents={agents}
              columnLabel="Skill"
              selectedId={openId}
              onOpen={setOpenId}
            />
          )}
        </div>
      </div>

      {opened && (
        <SkillDrawer
          skill={opened}
          agents={agents}
          onClose={() => setOpenId(null)}
          onDeleted={() => {
            setOpenId(null);
            setNotice(`Deleted ${opened.name}.`);
            void reload();
          }}
        />
      )}
      {modal === "new" && (
        <NewSkillModal
          onClose={() => setModal(null)}
          onCreated={(skill) => {
            setModal(null);
            setNotice(`Created ${skill.name}.`);
            void reload();
          }}
        />
      )}
      {modal === "import" && (
        <ImportUrlModal
          title="Import skill from URL"
          hint="Paste a link to a SKILL.md file. It is downloaded and added to your library."
          placeholder="https://example.com/skills/my-skill/SKILL.md"
          onImport={async (url) => (await api.skills.importFromUrl(url)).message}
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
