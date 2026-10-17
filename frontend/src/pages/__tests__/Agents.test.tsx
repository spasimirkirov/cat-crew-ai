import { act, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../lib/api";
import { useStore } from "../../state/store";
import { AgentsPage } from "../Agents";
import { agentsRecord, makeAgent, renderPage, seedStore, user } from "./helpers";

const cipher = makeAgent({ id: "cipher", name: "Cipher", title: "Prompt Engineer", role: "prompt_engineer", is_system: true, playbooks: ["prompt-craft", "review"], letta_tools: ["read_file"], description: "Turns briefs into blueprints." });
const pulse = makeAgent({ id: "pulse", name: "Pulse", title: "Full-Stack Developer", is_system: true, max_rounds: 12, essential_tools: ["read_file", "run_command", "write_file_patch"] });
const zed = makeAgent({ id: "zed", name: "Zed", title: "Data Wrangler", is_system: false, letta_tools: ["query_db", "plot"] });
const abe = makeAgent({ id: "abe", name: "Abe", title: "Helper", is_system: false });

let fullPrompts: Record<string, string>;

function seed() {
  seedStore({ agents: agentsRecord(zed, pulse, abe, cipher) });
}

beforeEach(() => {
  fullPrompts = { cipher: "You are Cipher.", pulse: "You are Pulse.", zed: "You are Zed.", abe: "You are Abe." };
  vi.spyOn(api.agents, "get").mockImplementation(async (id) => ({ ...useStore.getState().agents[id], system_prompt: fullPrompts[id] ?? "" }));
});
afterEach(() => {
  vi.restoreAllMocks();
});

const cards = () => screen.getAllByRole("button", { name: /^Open / }).map((b) => b.getAttribute("aria-label"));

describe("Agents page", () => {
  it("lists core cats first, then custom agents, with counts and the Core cat chip", () => {
    seed();
    renderPage(<AgentsPage />);
    expect(cards()).toEqual(["Open Cipher", "Open Pulse", "Open Abe", "Open Zed"]);

    const cipherCard = screen.getByRole("button", { name: "Open Cipher" });
    expect(within(cipherCard).getByText("Core cat")).toBeInTheDocument();
    expect(within(cipherCard).getByText("Prompt Engineer")).toBeInTheDocument();
    expect(within(cipherCard).getByText("2 playbooks")).toBeInTheDocument();
    expect(within(cipherCard).getByText("1 tool")).toBeInTheDocument();

    const zedCard = screen.getByRole("button", { name: "Open Zed" });
    expect(within(zedCard).queryByText("Core cat")).toBeNull();
    expect(within(zedCard).getByText("0 playbooks")).toBeInTheDocument();
    expect(within(zedCard).getByText("2 tools")).toBeInTheDocument();
  });

  it("shows a loading state until the store is ready", () => {
    seedStore({ ready: false });
    renderPage(<AgentsPage />);
    expect(screen.getByRole("status")).toHaveTextContent(/loading/i);
  });

  it("feeds each status pill from the live activity store", () => {
    seed();
    renderPage(<AgentsPage />);
    const card = screen.getByRole("button", { name: "Open Pulse" });
    expect(card.querySelector("[data-status]")).toHaveAttribute("data-status", "idle");
    act(() => useStore.setState({ activity: { pulse: { bot_id: "pulse", status: "coding", state: "working", text: "Editing", updated_at: null } } }));
    expect(card.querySelector("[data-status]")).toHaveAttribute("data-status", "coding");
    expect(within(card).getByText("Coding")).toBeInTheDocument();
  });

  it("opens a read-only panel: what the cat does, its strengths, tools, playbooks, and its instructions on demand", async () => {
    seed();
    renderPage(<AgentsPage />);
    await user.click(screen.getByRole("button", { name: "Open Cipher" }));
    const panel = await screen.findByRole("complementary", { name: "Cipher details" });
    expect(api.agents.get).toHaveBeenCalledWith("cipher");
    expect(await within(panel).findByText("Turns briefs into blueprints.")).toBeInTheDocument();
    expect(within(within(panel).getByRole("list", { name: "Playbooks" })).getAllByRole("listitem").map((l) => l.textContent)).toEqual(["prompt-craft", "review"]);
    expect(within(within(panel).getByRole("list", { name: "Letta tools" })).getByText("read_file")).toBeInTheDocument();
    // nothing to edit, save, delete -- and no per-cat round count
    expect(within(panel).queryByRole("textbox")).toBeNull();
    expect(within(panel).queryByRole("button", { name: /save|delete/i })).toBeNull();
    expect(within(panel).queryByText(/max rounds/i)).toBeNull();
    expect(within(panel).queryByText("You are Cipher.")).toBeNull();
    await user.click(within(panel).getByRole("button", { name: /How Cipher is instructed/ }));
    expect(within(panel).getByText("You are Cipher.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open Cipher" })).toHaveAttribute("aria-pressed", "true");
  });

  it("lists the tools a cat is never without, and hides the list when there are none", async () => {
    seed();
    renderPage(<AgentsPage />);
    await user.click(screen.getByRole("button", { name: "Open Pulse" }));
    const panel = await screen.findByRole("complementary", { name: "Pulse details" });
    const list = await within(panel).findByRole("list", { name: "Always on" });
    expect(within(list).getAllByRole("listitem").map((l) => l.textContent)).toEqual(["read_file", "run_command", "write_file_patch"]);
    await user.click(screen.getByRole("button", { name: "Open Cipher" }));
    const cipherPanel = await screen.findByRole("complementary", { name: "Cipher details" });
    await within(cipherPanel).findByText("Turns briefs into blueprints.");
    expect(within(cipherPanel).queryByRole("list", { name: "Always on" })).not.toBeInTheDocument();
  });

  it("has no way to create a new agent", () => {
    seed();
    renderPage(<AgentsPage />);
    expect(screen.queryByRole("button", { name: /new agent/i })).toBeNull();
  });

  it("switching agents loads the new one and closing hides the panel", async () => {
    seed();
    renderPage(<AgentsPage />);
    await user.click(screen.getByRole("button", { name: "Open Cipher" }));
    await screen.findByRole("complementary", { name: "Cipher details" });
    await user.click(screen.getByRole("button", { name: "Open Pulse" }));
    await screen.findByRole("complementary", { name: "Pulse details" });
    expect(screen.queryByRole("complementary", { name: "Cipher details" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Close details" }));
    expect(screen.queryByRole("complementary")).toBeNull();
  });
});
