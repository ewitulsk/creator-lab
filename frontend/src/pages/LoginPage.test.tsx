import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LoginPage } from "./LoginPage";

function renderLogin() {
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <LoginPage />
    </QueryClientProvider>,
  );
}

describe("LoginPage", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows the server's error on a bad password", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ error: "wrong username or password" }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      }),
    );
    renderLogin();
    await userEvent.type(screen.getByLabelText("Password"), "nope");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("wrong username or password")).toBeInTheDocument();
  });

  it("sends the CSRF header", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ user: { id: 1, username: "admin" } }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    renderLogin();
    await userEvent.type(screen.getByLabelText("Password"), "secret-pass");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await vi.waitFor(() => expect(spy).toHaveBeenCalled());
    const req = spy.mock.calls[0][0] as Request;
    expect(req.headers.get("X-Requested-With")).toBe("creator-lab");
  });
});
