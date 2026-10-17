import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { HostModel } from "../../../lib/types";
import { user } from "../../../pages/__tests__/helpers";
import { ModelPicker } from "../ModelPicker";

const m = (name: string, capabilities: string[], over: Partial<HostModel> = {}): HostModel => ({
  name,
  display_name: name,
  loaded: false,
  capabilities,
  context_length: 0,
  ...over,
});

function Picker(props: { models: HostModel[]; value?: string; onChange?: (name: string) => void }) {
  const onChange = props.onChange ?? vi.fn();
  return <ModelPicker id="model" models={props.models} value={props.value ?? ""} onChange={onChange} />;
}

const box = () => screen.getByRole("combobox");
const open = async () => user.click(screen.getByRole("combobox"));
const search = () => screen.getByRole("textbox", { name: "Search models" });
const options = () => within(screen.getByRole("listbox", { name: "Models" })).queryAllByRole("option");
const optionNames = () => options().map((o) => o.querySelector(".truncate")?.textContent);

describe("ModelPicker", () => {
  it("sorts models cheapest-first by input price, then output price as the tiebreak", async () => {
    const models = [
      m("expensive-vision", ["vision", "chat"], { price_prompt_per_1m: 5, price_completion_per_1m: 10 }),
      m("cheap-tools", ["tools", "chat"], { price_prompt_per_1m: 0.1, price_completion_per_1m: 0.2 }),
      m("mid-tools", ["tools", "chat"], { price_prompt_per_1m: 1, price_completion_per_1m: 1 }),
      m("free-chat", ["chat"], { price_prompt_per_1m: 0, price_completion_per_1m: 0, is_free: true }),
      m("no-price-model", ["chat"]),
      // same input price, different output price: output breaks the tie
      m("tie-high-output", ["chat"], { price_prompt_per_1m: 2, price_completion_per_1m: 5 }),
      m("tie-low-output", ["chat"], { price_prompt_per_1m: 2, price_completion_per_1m: 1 }),
    ];
    render(<Picker models={models} />);
    await open();
    expect(optionNames()).toEqual(["free-chat", "cheap-tools", "mid-tools", "tie-low-output", "tie-high-output", "expensive-vision", "no-price-model"]);
  });

  it("keeps unpriced models in their original relative order (stable sort)", async () => {
    const models = [m("alpha", ["chat"]), m("beta", ["tools", "chat"]), m("gamma", ["vision", "chat"])];
    render(<Picker models={models} />);
    await open();
    expect(optionNames()).toEqual(["alpha", "beta", "gamma"]);
  });

  it("filters the open list as the user types in the search field", async () => {
    const models = [m("gpt-omega", ["chat"]), m("claude-sigma", ["chat"]), m("gpt-delta", ["chat"])];
    render(<Picker models={models} />);
    await open();
    expect(optionNames()).toHaveLength(3);
    fireEvent.change(search(), { target: { value: "gpt" } });
    expect(optionNames()).toEqual(["gpt-omega", "gpt-delta"]);
    fireEvent.change(search(), { target: { value: "GPT-DELTA" } }); // case-insensitive
    expect(optionNames()).toEqual(["gpt-delta"]);
  });

  it("shows a message and no options when nothing matches the search", async () => {
    render(<Picker models={[m("solo-model", ["chat"])]} />);
    await open();
    fireEvent.change(search(), { target: { value: "nothing-like-this" } });
    expect(options()).toHaveLength(0);
    expect(screen.getByText("No models match.")).toBeInTheDocument();
  });

  it("resets the search text (but not the checkbox filters) every time the dropdown reopens", async () => {
    const models = [m("alpha", ["vision", "chat"]), m("beta", ["chat"])];
    render(<Picker models={models} />);
    await open();
    fireEvent.change(search(), { target: { value: "alpha" } });
    expect(optionNames()).toEqual(["alpha"]);
    await user.click(screen.getByLabelText("Vision")); // set a filter too
    fireEvent.keyDown(box(), { key: "Escape" });
    await open();
    expect((search() as HTMLInputElement).value).toBe(""); // query cleared
    expect(screen.getByLabelText("Vision")).toBeChecked(); // filter kept
    expect(optionNames()).toEqual(["alpha"]); // vision filter still narrows it
  });

  it("filters with the Vision, Tools and Free checkboxes, combinable", async () => {
    const models = [
      m("vision-only", ["vision", "chat"]),
      m("tools-only", ["tools", "chat"]),
      m("vision-and-tools", ["vision", "tools", "chat"]),
      m("free-one", ["chat"], { is_free: true }),
      m("plain", ["chat"]),
    ];
    render(<Picker models={models} />);
    await open();

    await user.click(screen.getByLabelText("Vision"));
    expect(optionNames()).toEqual(["vision-only", "vision-and-tools"]);

    await user.click(screen.getByLabelText("Tools")); // now Vision AND Tools
    expect(optionNames()).toEqual(["vision-and-tools"]);

    await user.click(screen.getByLabelText("Vision"));
    await user.click(screen.getByLabelText("Tools"));
    await user.click(screen.getByLabelText("Free"));
    expect(optionNames()).toEqual(["free-one"]);
  });

  it("shows only the top vendors' models until All vendors is ticked", async () => {
    const models = [m("deepseek/deepseek-v4-pro", ["tools"]), m("someone/tiny-finetune", ["tools"]), m("anthropic/claude-x", ["tools"])];
    render(<Picker models={models} />);
    await open();
    expect(optionNames()).toEqual(["deepseek/deepseek-v4-pro", "anthropic/claude-x"]);
    await user.click(screen.getByLabelText("All vendors"));
    expect(optionNames()).toHaveLength(3);
  });

  it("a required capability always filters and has no checkbox", async () => {
    const models = [m("seer", ["vision", "chat"]), m("blind", ["tools", "chat"])];
    render(<ModelPicker id="model" models={models} value="" onChange={vi.fn()} require="vision" />);
    await open();
    expect(optionNames()).toEqual(["seer"]);
    expect(screen.queryByLabelText("Vision")).toBeNull();
    expect(screen.getByLabelText("Tools")).toBeInTheDocument();
  });

  it("filters reasoning models with the Reasoning checkbox", async () => {
    const models = [m("thinker", ["reasoning", "tools", "chat"]), m("plain", ["tools", "chat"])];
    render(<Picker models={models} />);
    await open();
    await user.click(screen.getByLabelText("Reasoning"));
    expect(optionNames()).toEqual(["thinker"]);
  });

  it("puts every badge together, with the price shown only afterward", async () => {
    const models = [m("priced-model", ["tools"], { price_prompt_per_1m: 0.14, price_completion_per_1m: 0.28, supports_prompt_cache: true })];
    render(<Picker models={models} />);
    await open();
    const row = options()[0];
    const text = row.textContent ?? "";
    expect(within(row).getByText("Tools")).toBeInTheDocument();
    expect(within(row).getByText("Cache")).toBeInTheDocument();
    expect(within(row).getByText("$0.14 / $0.28")).toHaveAttribute("title", "$0.14 / $0.28 per 1M tokens (input / output)");
    // badges (Tools, then Cache) both come before the price text
    expect(text.indexOf("Tools")).toBeLessThan(text.indexOf("Cache"));
    expect(text.indexOf("Cache")).toBeLessThan(text.indexOf("$0.14"));
  });

  it("shows a Free badge instead of price text for free models", async () => {
    const models = [m("free-model", ["chat"], { is_free: true, price_prompt_per_1m: 0, price_completion_per_1m: 0 })];
    render(<Picker models={models} />);
    await open();
    const row = options()[0];
    expect(within(row).getByText("Free")).toBeInTheDocument();
    expect(within(row).queryByText(/per 1M/)).toBeNull();
  });

  it("picks a filtered result by clicking it", async () => {
    const onChange = vi.fn();
    const models = [m("alpha", ["chat"]), m("beta", ["chat"])];
    render(<Picker models={models} onChange={onChange} />);
    await open();
    fireEvent.change(search(), { target: { value: "bet" } });
    await user.click(within(screen.getByRole("listbox", { name: "Models" })).getByRole("option", { name: /beta/ }));
    expect(onChange).toHaveBeenCalledWith("beta");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("navigates the filtered list with the arrow keys from the search field", async () => {
    const onChange = vi.fn();
    const models = [m("alpha", ["chat"]), m("beta", ["chat"]), m("gamma", ["chat"])];
    render(<Picker models={models} onChange={onChange} />);
    await open();
    const input = search();
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith("gamma");
  });
});
