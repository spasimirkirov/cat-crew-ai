import { faArrowLeft, faFolderOpen } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { Link, useParams } from "react-router-dom";

import { ProjectModelOverrides } from "../components/project/ProjectModelOverrides";
import { EmptyState, PageHeader } from "../components/Ui";
import { useStore } from "../state/store";

/** `/project/:pid/settings`: per-project settings. For now, the model overrides. */
export function ProjectSettingsPage() {
  const { pid = "" } = useParams();
  const ready = useStore((s) => s.ready);
  const project = useStore((s) => s.projects.find((p) => p.id === pid));

  if (ready && !project) {
    return (
      <div className="p-8">
        <EmptyState icon={faFolderOpen} title="Project not found" hint="It may have been deleted." />
        <div className="mt-4 text-center">
          <Link to="/" className="btn">
            Home
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title={project ? `${project.name} · Settings` : "Project settings"}
        subtitle="Settings that apply to this project only."
        actions={
          <Link to={`/project/${pid}`} className="btn btn-sm btn-ghost">
            <FontAwesomeIcon icon={faArrowLeft} /> Back to project
          </Link>
        }
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-8 py-6">
        <div className="mx-auto flex max-w-5xl flex-col gap-4">
          <ProjectModelOverrides projectId={pid} />
        </div>
      </div>
    </div>
  );
}
