import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "@/app";

describe("App", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.pushState({}, "", "/");
  });

  it("presents the scientific workflow on the public landing page", () => {
    render(<App />);

    expect(
      screen.getByRole("heading", {
        name: "Da imagem bruta ao resultado científico.",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /Iniciar uma análise/ }),
    ).toHaveAttribute("href", "/login");
  });

  it("renders the dashboard with persisted API data", async () => {
    window.history.pushState({}, "", "/dashboard");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            project_count: 0,
            dataset_count: 0,
            recent_projects: [],
          }),
          { status: 200 },
        ),
      ),
    );

    render(<App />);

    expect(
      screen.getByRole("heading", {
        name: "Dashboard",
      }),
    ).toBeInTheDocument();
    expect(
      await screen.findByText("Ainda não há dados para exibir."),
    ).toBeInTheDocument();
  });

  it("renders the results overview from the processing API", async () => {
    window.history.pushState({}, "", "/processing-jobs/job-1/results");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            overview: {
              dataset_id: "dataset-1",
              dataset_name: "Milho",
              treatment_count: 2,
              repetition_count: 4,
              image_count: 20,
              indices: ["NDVI"],
              analyzed_pixels: 1200,
              error_count: 0,
              analyzed_at: "2026-08-31T12:00:00Z",
            },
            table: { rows: [], total: 0, treatments: [], indices: ["NDVI"] },
            treatment_summary: [],
            images: [],
            errors: [],
            files: [],
            reproducibility: {
              job_id: "job-1",
              job_state: "completed",
              created_at: "2026-08-31T12:00:00Z",
              completed_at: "2026-08-31T12:01:00Z",
              configuration: {},
              inputs: [],
            },
          }),
          { status: 200 },
        ),
      ),
    );

    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "Resultados" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Milho")).toBeInTheDocument();
  });
});
