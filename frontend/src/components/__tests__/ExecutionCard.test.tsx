import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ExecutionCard, humanStep } from "../ExecutionCard";

const steps = [
  { line: "Thinking" },
  { line: "write_file index.html", preview: "<h1>Cat Cafe</h1>" },
  { line: "run_command npm test", preview: "3 passed" },
];

describe("ExecutionCard", () => {
  it("shows only the latest step, clamped to two lines", () => {
    render(<ExecutionCard steps={steps} running />);
    const summary = screen.getByTestId("execution-summary");
    expect(summary).toHaveClass("line-clamp-2");
    expect(summary).toHaveTextContent("run_command npm test");
    expect(summary).not.toHaveTextContent("3 passed"); // raw output stays in the expanded log
    expect(screen.queryByTestId("execution-log")).toBeNull();
    expect(screen.getByRole("button", { name: /see more/i })).toHaveAttribute("aria-expanded", "false");
  });

  it("See more reveals the full numbered log and any attached children; Show less hides it again", () => {
    render(
      <ExecutionCard steps={steps}>
        <div>diff goes here</div>
      </ExecutionCard>,
    );
    fireEvent.click(screen.getByRole("button", { name: /see more/i }));
    const log = screen.getByTestId("execution-log");
    expect(log.querySelectorAll("li")).toHaveLength(3);
    expect(log).toHaveTextContent("<h1>Cat Cafe</h1>");
    expect(screen.getByText("diff goes here")).toBeInTheDocument();
    // the collapsed summary stays clamped while expanded
    expect(screen.getByTestId("execution-summary")).toHaveClass("line-clamp-2");

    fireEvent.click(screen.getByRole("button", { name: /show less/i }));
    expect(screen.queryByTestId("execution-log")).toBeNull();
  });

  it("has no toggle when there is nothing more to show", () => {
    render(<ExecutionCard steps={[{ line: "Thinking" }]} />);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("falls back to a neutral line when there are no steps yet", () => {
    const { rerender } = render(<ExecutionCard steps={[]} running />);
    expect(screen.getByTestId("execution-summary")).toHaveTextContent("Working…");
    rerender(<ExecutionCard steps={[]} />);
    expect(screen.getByTestId("execution-summary")).toHaveTextContent("No activity recorded");
  });
});

describe("ExecutionCard: questions and what is happening now", () => {
  it("a question the cat asked reads as a conversation, never as the raw arguments", () => {
    const old = {
      line: "ask_user",
      preview: 'args: {"choices": ["Livewire", "Inertia"], "prompt": "Do you want Jetstream with Livewire?"}\nresult: User answered: Livewire',
    };
    expect(humanStep(old)).toEqual({ line: "Asked you: Do you want Jetstream with Livewire?", preview: "You answered: Livewire" });
    render(<ExecutionCard steps={[old]} />);
    expect(screen.getByTestId("execution-summary")).toHaveTextContent("Asked you: Do you want Jetstream with Livewire?");
    expect(screen.getByTestId("execution-summary")).not.toHaveTextContent("args");
  });

  it("a live card says what is happening right now", () => {
    render(<ExecutionCard steps={steps} running now="Waiting for your answer below" />);
    expect(screen.getByTestId("execution-now")).toHaveTextContent("Now: Waiting for your answer below");
  });

  it("a finished card has no 'now' line", () => {
    render(<ExecutionCard steps={steps} now="Thinking" />);
    expect(screen.queryByTestId("execution-now")).toBeNull();
  });
});
