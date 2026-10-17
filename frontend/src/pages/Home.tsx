// Home is a launcher: it starts, opens and manages projects. It deliberately shows no
// work items of any kind; those live inside a project (Cat Crew + the tasks panel).

import { faFolderOpen, faPlus, faRocket } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { FolderBrowserModal } from "../components/home/FolderBrowser";
import { NewProjectModal } from "../components/home/NewProjectModal";
import { ProjectCard } from "../components/home/ProjectCard";
import { DeleteProjectModal, RenameProjectModal } from "../components/home/ProjectDialogs";
import { folderBasename, sortProjects } from "../components/home/projectMeta";
import { EmptyState, ErrorNote, PageHeader, Spinner } from "../components/Ui";
import { api } from "../lib/api";
import type { Project } from "../lib/types";
import { useNow } from "../lib/useNow";
import { useStore } from "../state/store";

/** Put a project into the store right away (the `project.changed` event would do it a moment later). */
function upsertProject(project: Project) {
  useStore.setState((s) => {
    const i = s.projects.findIndex((p) => p.id === project.id);
    if (i === -1) return { projects: [...s.projects, project] };
    const projects = s.projects.slice();
    projects[i] = project;
    return { projects };
  });
}

export function HomePage() {
  const navigate = useNavigate();
  const now = useNow();
  const ready = useStore((s) => s.ready);
  const projects = useStore((s) => s.projects);
  const lastError = useStore((s) => s.lastError);

  const [showNew, setShowNew] = useState(false);
  const [showOpen, setShowOpen] = useState(false);
  const [renaming, setRenaming] = useState<Project | null>(null);
  const [deleting, setDeleting] = useState<Project | null>(null);

  useEffect(() => {
    void useStore.getState().refreshProjects();
  }, []);

  const sorted = useMemo(() => sortProjects(projects), [projects]);

  const openProject = (p: Project) => navigate(`/project/${encodeURIComponent(p.id)}`);

  const created = (p: Project) => {
    upsertProject(p);
    setShowNew(false);
    openProject(p);
  };

  const openFolder = async (path: string) => {
    const p = await api.projects.create({ name: folderBasename(path), folders: [path] });
    upsertProject(p);
    setShowOpen(false);
    openProject(p);
  };

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="CatCrewAI"
        subtitle="Start a project, or pick up where your crew left off."
        actions={
          <>
            <button type="button" className="btn" onClick={() => setShowOpen(true)}>
              <FontAwesomeIcon icon={faFolderOpen} /> Open folder
            </button>
            <button type="button" className="btn btn-primary" onClick={() => setShowNew(true)}>
              <FontAwesomeIcon icon={faPlus} /> New project
            </button>
          </>
        }
      />

      <div className="min-h-0 flex-1 overflow-y-auto px-8 py-6">
        {!ready ? (
          lastError ? (
            <div className="mx-auto flex max-w-md flex-col items-center gap-3 py-16">
              <ErrorNote message={lastError} />
              <button type="button" className="btn" onClick={() => void useStore.getState().bootstrap()}>
                Try again
              </button>
            </div>
          ) : (
            <div role="status" className="flex items-center justify-center gap-2 py-24 text-sm text-muted">
              <Spinner /> Loading your projects
            </div>
          )
        ) : sorted.length === 0 ? (
          <EmptyState
            icon={faRocket}
            title="No projects yet"
            hint="Create a project to put the crew to work, or open a folder you already have."
            action={
              <button type="button" className="btn btn-primary mt-2" onClick={() => setShowNew(true)}>
                <FontAwesomeIcon icon={faPlus} /> New project
              </button>
            }
          />
        ) : (
          <section aria-labelledby="recent-projects">
            <div className="mb-4 flex items-baseline gap-2">
              <h2 id="recent-projects" className="text-sm font-semibold">
                Recent projects
              </h2>
              <span className="text-xs text-muted">{sorted.length}</span>
            </div>
            <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-4">
              {sorted.map((p) => (
                <ProjectCard key={p.id} project={p} now={now} onOpen={openProject} onRename={setRenaming} onDelete={setDeleting} />
              ))}
            </div>
          </section>
        )}
      </div>

      {showNew && <NewProjectModal onClose={() => setShowNew(false)} onCreated={created} />}
      {showOpen && <FolderBrowserModal title="Open a folder as a project" pickLabel="Open this folder" onClose={() => setShowOpen(false)} onPick={openFolder} />}
      {renaming && (
        <RenameProjectModal
          project={renaming}
          onClose={() => setRenaming(null)}
          onSaved={(p) => {
            upsertProject(p);
            setRenaming(null);
          }}
        />
      )}
      {deleting && (
        <DeleteProjectModal
          project={deleting}
          onClose={() => setDeleting(null)}
          onDeleted={(p) => {
            useStore.setState((s) => ({ projects: s.projects.filter((x) => x.id !== p.id) }));
            setDeleting(null);
          }}
        />
      )}
    </div>
  );
}
