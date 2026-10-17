import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../lib/api";
import { seedStore } from "../../pages/__tests__/helpers";
import { Composer } from "../room/Composer";

beforeEach(() => {
  seedStore();
  vi.restoreAllMocks();
});

const view = (isRoom = false) => (
  <Composer projectId="p1" botId={isRoom ? "crew" : "pulse"} botName={isRoom ? "Cat Crew" : "Pulse"} isRoom={isRoom} turn={null} />
);

describe("Composer: report a problem", () => {
  it("sends the text to the report endpoint of that cat's thread, not as a normal message", async () => {
    const report = vi.spyOn(api.chat, "report").mockResolvedValue({ turn_id: "t1", queued: false });
    const send = vi.spyOn(api.chat, "send").mockResolvedValue({ turn_id: "t2", queued: false });
    render(view());
    const box = screen.getByLabelText("Message Pulse");
    const button = screen.getByRole("button", { name: /report problem/i });
    expect(button).toBeDisabled(); // nothing typed yet
    fireEvent.change(box, { target: { value: "the login button does nothing" } });
    fireEvent.click(button);
    await waitFor(() => expect(report).toHaveBeenCalledWith("p1", "pulse", "the login button does nothing"));
    expect(send).not.toHaveBeenCalled();
    expect(await screen.findByText(/team lead is writing the fix plan/i)).toBeInTheDocument();
    expect((box as HTMLTextAreaElement).value).toBe("");
  });

  it("is available in Cat Crew too, and shows a failure instead of clearing the text", async () => {
    vi.spyOn(api.chat, "report").mockRejectedValue(new Error("boom"));
    render(view(true));
    const box = screen.getByLabelText("Message Cat Crew") as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "site is blank" } });
    fireEvent.click(screen.getByRole("button", { name: /report problem/i }));
    await waitFor(() => expect(api.chat.report).toHaveBeenCalledWith("p1", "crew", "site is blank"));
    expect(box.value).toBe("site is blank");
  });
});
