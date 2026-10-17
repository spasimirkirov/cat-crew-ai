import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { estimateLines } from "../CollapsibleText";
import { MessageView } from "../room/MessageView";
import type { Message } from "../../lib/types";

const NOW = Date.parse("2026-09-20T12:00:00Z");
const cat = { name: "Pulse", avatar: "", color: "#22d3ee" };

const lines = (n: number, prefix = "line") => Array.from({ length: n }, (_, i) => `${prefix} ${i + 1}`).join("\n");
const msg = (over: Partial<Message>): Message => ({ id: "m1", role: "assistant", text: "", created_at: "2026-09-20T11:55:00Z", bot_id: "pulse", speaker: "Pulse", ...over });
const show = (m: Message) => render(<MessageView message={m} agent={cat} now={NOW} />);

describe("estimateLines", () => {
  it("counts source lines and wraps long ones", () => {
    expect(estimateLines("")).toBe(0);
    expect(estimateLines("one\ntwo\nthree")).toBe(3);
    expect(estimateLines("x".repeat(200))).toBe(3); // ~90 columns per line
    expect(estimateLines("a\n\nb")).toBe(3); // a blank line still takes a line
  });
});

describe("long messages collapse behind See more", () => {
  it("leaves short messages alone, with no toggle", () => {
    show(msg({ text: "All done.", phase: "build" }));
    expect(screen.queryByRole("button", { name: /see more/i })).toBeNull();
    expect(screen.getByTestId("message-text")).toHaveAttribute("data-collapsed", "false");
  });

  it("collapses a long work report to a short card and expands it on demand", () => {
    const text = lines(30, "step");
    show(msg({ text, phase: "build" }));
    const body = screen.getByTestId("message-text");
    expect(body).toHaveAttribute("data-collapsed", "true");
    expect(body.style.maxHeight).not.toBe("");
    expect(body).toHaveTextContent("step 30"); // the whole text stays in the DOM: only the view is clipped

    const more = screen.getByRole("button", { name: "See more (25 more lines)" });
    expect(more).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(more);
    expect(body).toHaveAttribute("data-collapsed", "false");
    expect(body.style.maxHeight).toBe("");
    const less = screen.getByRole("button", { name: "Show less" });
    expect(less).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(less);
    expect(body).toHaveAttribute("data-collapsed", "true");
  });

  it("does not bother collapsing a report that is only a line or two over", () => {
    show(msg({ text: lines(7), phase: "qa" })); // shows 5, hides 2: not worth a button
    expect(screen.queryByRole("button", { name: /see more/i })).toBeNull();
    show(msg({ id: "m2", text: lines(8), phase: "qa" }));
    expect(screen.getAllByRole("button", { name: /see more/i })).toHaveLength(1);
  });

  it("lets a conversation reply run longer than a work report before collapsing", () => {
    show(msg({ text: lines(13) })); // a chat reply: up to ~12 lines read freely
    expect(screen.queryByRole("button", { name: /see more/i })).toBeNull();
    show(msg({ id: "m2", text: lines(40) }));
    expect(screen.getByRole("button", { name: "See more (28 more lines)" })).toBeInTheDocument();
  });

  it("counts a fenced code block by its lines", () => {
    show(msg({ text: "Here it is:\n```ts\n" + lines(20, "code") + "\n```", phase: "design" }));
    expect(screen.getByRole("button", { name: /see more/i })).toBeInTheDocument();
  });

  it("collapses a very long message from the user too", () => {
    render(<MessageView message={{ id: "u1", role: "user", text: lines(40, "paste"), created_at: "2026-09-20T11:59:00Z" }} now={NOW} />);
    expect(screen.getByRole("button", { name: /see more/i })).toBeInTheDocument();
  });

  it("keeps the activity card and its own See more separate from the message text", () => {
    show(msg({ text: lines(30), phase: "build", activity: { elapsed_s: 5, steps: [{ line: "write_file a.ts" }, { line: "run_tests" }] } }));
    const card = screen.getByTestId("execution-card");
    expect(within(card).getByRole("button", { name: /see more/i })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /see more/i })).toHaveLength(2);
    fireEvent.click(within(card).getByRole("button", { name: /see more/i }));
    expect(screen.getByTestId("message-text")).toHaveAttribute("data-collapsed", "true"); // independent toggles
  });
});
