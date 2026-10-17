import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ContextMenu, editableOf } from "../ContextMenu";

const writeText = vi.fn().mockResolvedValue(undefined);
const readText = vi.fn().mockResolvedValue("pasted");
let exec: ReturnType<typeof vi.fn>;

beforeEach(() => {
  writeText.mockReset().mockResolvedValue(undefined); // restoreAllMocks wipes implementations
  readText.mockReset().mockResolvedValue("pasted");
  Object.defineProperty(navigator, "clipboard", { value: { writeText, readText }, configurable: true });
  exec = vi.fn(() => true);
  (document as unknown as { execCommand: unknown }).execCommand = exec;
});
afterEach(() => vi.restoreAllMocks());

function page(extra = "") {
  return render(
    <div>
      <ContextMenu />
      <p data-testid="text">Some plain text</p>
      <input aria-label="name" defaultValue="hello world" />
      <input aria-label="secret" type="password" defaultValue="hunter2" />
      <input aria-label="locked" readOnly defaultValue="fixed" />
      <input aria-label="check" type="checkbox" />
      <textarea aria-label="notes" defaultValue="one two" />
      {extra}
    </div>,
  );
}

/** Right click; returns true when the browser's own menu was suppressed. */
const rightClick = (el: Element, init: object = {}) => !fireEvent.contextMenu(el, { clientX: 40, clientY: 50, ...init });
const select = (el: HTMLInputElement, from: number, to: number) => {
  el.focus();
  el.setSelectionRange(from, to);
};

describe("ContextMenu", () => {
  it("suppresses the webview's own menu everywhere, and shows none when there is nothing to do", () => {
    page();
    expect(rightClick(screen.getByTestId("text"))).toBe(true);
    expect(screen.queryByRole("menu")).toBeNull();
    expect(rightClick(document.body)).toBe(true);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("a text field gets Cut, Copy, Paste and Select all", () => {
    page();
    rightClick(screen.getByLabelText("name"));
    const menu = screen.getByRole("menu", { name: "Edit" });
    expect(Array.from(menu.querySelectorAll("[role=menuitem]")).map((i) => i.textContent?.replace(/(Ctrl\+|⌘)\w$/, ""))).toEqual(["Cut", "Copy", "Paste", "Select all"]);
  });

  it("Cut and Copy need a selection; Copy puts exactly the selected text on the clipboard", async () => {
    page();
    const input = screen.getByLabelText("name") as HTMLInputElement;
    rightClick(input);
    expect(screen.getByRole("menuitem", { name: /Cut/ })).toBeDisabled();
    expect(screen.getByRole("menuitem", { name: /Copy/ })).toBeDisabled();
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });

    select(input, 0, 5);
    rightClick(input);
    await act(async () => {
      fireEvent.click(screen.getByRole("menuitem", { name: /Copy/ }));
    });
    expect(writeText).toHaveBeenCalledWith("hello");
    expect(screen.queryByRole("menu")).toBeNull(); // choosing closes it
  });

  it("Cut uses the editing command so the field (and React) sees the change", async () => {
    page();
    const input = screen.getByLabelText("name") as HTMLInputElement;
    select(input, 6, 11);
    rightClick(input);
    await act(async () => {
      fireEvent.click(screen.getByRole("menuitem", { name: /Cut/ }));
    });
    expect(exec).toHaveBeenCalledWith("cut");
  });

  it("Paste inserts the clipboard text at the cursor", async () => {
    page();
    rightClick(screen.getByLabelText("notes"));
    await act(async () => {
      fireEvent.click(screen.getByRole("menuitem", { name: /Paste/ }));
    });
    expect(readText).toHaveBeenCalled();
    await waitFor(() => expect(exec).toHaveBeenCalledWith("insertText", false, "pasted"));
  });

  it("Select all selects the whole field", async () => {
    page();
    const input = screen.getByLabelText("name") as HTMLInputElement;
    rightClick(input);
    await act(async () => {
      fireEvent.click(screen.getByRole("menuitem", { name: /Select all/ }));
    });
    expect([input.selectionStart, input.selectionEnd]).toEqual([0, 11]);
  });

  it("never copies or cuts a password, and a read-only field cannot be cut or pasted into", () => {
    page();
    const secret = screen.getByLabelText("secret") as HTMLInputElement;
    select(secret, 0, 7);
    rightClick(secret);
    expect(screen.getByRole("menuitem", { name: /Cut/ })).toBeDisabled();
    expect(screen.getByRole("menuitem", { name: /Copy/ })).toBeDisabled();
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });

    const locked = screen.getByLabelText("locked") as HTMLInputElement;
    select(locked, 0, 3);
    rightClick(locked);
    expect(screen.getByRole("menuitem", { name: /Cut/ })).toBeDisabled();
    expect(screen.getByRole("menuitem", { name: /Paste/ })).toBeDisabled();
    expect(screen.getByRole("menuitem", { name: /Copy/ })).toBeEnabled();
  });

  it("selected page text (chat, terminal output) can be copied", async () => {
    page();
    vi.spyOn(window, "getSelection").mockReturnValue({ toString: () => "chosen words" } as Selection);
    rightClick(screen.getByTestId("text"));
    const items = screen.getAllByRole("menuitem");
    expect(items).toHaveLength(1);
    await act(async () => {
      fireEvent.click(items[0]);
    });
    expect(writeText).toHaveBeenCalledWith("chosen words");
  });

  it("closes on Escape, on a click elsewhere and on scroll", () => {
    page();
    const input = screen.getByLabelText("name");
    rightClick(input);
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    rightClick(input);
    fireEvent.mouseDown(screen.getByTestId("text"));
    expect(screen.queryByRole("menu")).toBeNull();
    rightClick(input);
    fireEvent.scroll(window);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("moves with the arrow keys and Enter runs the highlighted item", async () => {
    page();
    const input = screen.getByLabelText("name") as HTMLInputElement;
    select(input, 0, 5);
    rightClick(input);
    const menu = screen.getByRole("menu");
    fireEvent.keyDown(menu, { key: "ArrowDown" }); // Cut -> Copy
    await act(async () => {
      fireEvent.keyDown(menu, { key: "Enter" });
    });
    expect(writeText).toHaveBeenCalledWith("hello");
  });

  it("lets a developer keep the native menu with Shift in a development build", () => {
    page();
    expect(rightClick(screen.getByLabelText("name"), { shiftKey: true })).toBe(false);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("knows which elements are text fields", () => {
    page();
    expect(editableOf(screen.getByLabelText("name"))).not.toBeNull();
    expect(editableOf(screen.getByLabelText("notes"))).not.toBeNull();
    expect(editableOf(screen.getByLabelText("check"))).toBeNull();
    expect(editableOf(screen.getByTestId("text"))).toBeNull();
    expect(editableOf(null)).toBeNull();
  });
});
