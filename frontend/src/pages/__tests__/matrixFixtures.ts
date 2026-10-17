import type { Agent, Skill, Tool } from "../../lib/types";
import { useStore } from "../../state/store";
import { initialCore } from "../../state/reducer";

export function agent(id: string, over: Partial<Agent> = {}): Agent {
  return {
    id,
    name: id.charAt(0).toUpperCase() + id.slice(1),
    role: "specialist",
    title: "",
    description: "",
    avatar: "",
    color: "#38BDF8",
    skills: [],
    playbooks: [],
    tools: [],
    letta_tools: [],
    mcp_tools: [],
    max_rounds: 8,
    is_system: true,
    ...over,
  };
}

// A non-core cat is listed first on purpose: the matrix must still put core cats first.
export const AGENTS: Agent[] = [
  agent("scout", { is_system: false }),
  agent("pulse"),
  agent("aegis"),
  agent("pixel"),
];

export function skill(id: string, name: string, over: Partial<Skill> = {}): Skill {
  return {
    id,
    name,
    description: `${name} description`,
    type: "custom",
    version: "1.0.0",
    target_roles: [],
    playbook_path: `backend/skills/${name}/SKILL.md`,
    source_url: "",
    is_global: false,
    agent_ids: [],
    ...over,
  };
}

export function tool(id: string, name: string, over: Partial<Tool> = {}): Tool {
  return {
    id,
    name,
    description: `${name} description`,
    handler: `backend.tool_functions.demo:${name}`,
    is_global: false,
    agent_ids: [],
    ...over,
  };
}

/** Reset the zustand store to a known set of agents (and no proposals). */
export function seedStore(agents: Agent[] = AGENTS) {
  useStore.setState({ ...initialCore(), agents: Object.fromEntries(agents.map((a) => [a.id, a])) });
}
