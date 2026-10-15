import { describe, expect, it } from "vitest";
import { exportUrl } from "./data";

describe("exportUrl", () => {
  it("builds the download path and drops empty filters", () => {
    expect(exportUrl("videos", "csv")).toBe("/api/exports/videos.csv");
    expect(exportUrl("analyzer", "parquet", { analyzer: "outlier_score@1", channel_id: undefined })).toBe(
      "/api/exports/analyzer.parquet?analyzer=outlier_score%401",
    );
  });
});
