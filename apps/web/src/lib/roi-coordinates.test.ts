import { describe, expect, it } from "vitest";

import {
  canvasToNormalized,
  constrainImagePan,
  imageToNormalized,
  normalizedToCanvas,
  normalizedToImage,
  polygonPixelArea,
  zoomImageAt,
  type ImageViewport,
} from "./roi-coordinates";

describe("ROI coordinate conversion", () => {
  const view: ImageViewport = {
    viewportWidth: 1200,
    viewportHeight: 760,
    imageWidth: 5472,
    imageHeight: 3648,
    zoom: 2.75,
    panX: 83.25,
    panY: -47.5,
  };

  it("converts canvas → normalized → original pixels without CSS-size leakage", () => {
    const normalized = canvasToNormalized({ x: 730.125, y: 411.875 }, view);
    const image = normalizedToImage(
      normalized,
      view.imageWidth,
      view.imageHeight,
    );
    const roundTrip = imageToNormalized(
      image,
      view.imageWidth,
      view.imageHeight,
    );

    expect(roundTrip.x).toBeCloseTo(normalized.x, 12);
    expect(roundTrip.y).toBeCloseTo(normalized.y, 12);
    expect(normalizedToCanvas(roundTrip, view).x).toBeCloseTo(730.125, 9);
    expect(normalizedToCanvas(roundTrip, view).y).toBeCloseTo(411.875, 9);
  });

  it("keeps the same original pixel across different preview resolutions", () => {
    const sourcePixel = { x: 1832.375, y: 2987.625 };
    const normalized = imageToNormalized(
      sourcePixel,
      view.imageWidth,
      view.imageHeight,
    );
    const compactView = {
      ...view,
      viewportWidth: 700,
      viewportHeight: 420,
      zoom: 1.4,
      panX: -31,
      panY: 19,
    };
    const compactCanvas = normalizedToCanvas(normalized, compactView);
    const recovered = normalizedToImage(
      canvasToNormalized(compactCanvas, compactView),
      view.imageWidth,
      view.imageHeight,
    );

    expect(recovered.x).toBeCloseTo(sourcePixel.x, 9);
    expect(recovered.y).toBeCloseTo(sourcePixel.y, 9);
  });

  it("keeps the same original pixel below the cursor while zooming", () => {
    const anchor = { x: 183.25, y: 624.5 };
    const before = canvasToNormalized(anchor, view, false);
    const zoomed = zoomImageAt(view, 4.25, anchor);
    const after = canvasToNormalized(anchor, zoomed, false);

    expect(after.x).toBeCloseTo(before.x, 12);
    expect(after.y).toBeCloseTo(before.y, 12);
  });

  it("constrains pan without adding drift on the fitted axis", () => {
    const constrained = constrainImagePan({
      ...view,
      viewportWidth: 800,
      viewportHeight: 640,
      imageWidth: 1600,
      imageHeight: 1300,
      zoom: 1,
      panX: -999,
      panY: 999,
    });

    expect(constrained.panX).toBe(0);
    expect(constrained.panY).toBe(0);
  });

  it("calculates rectangle area on the original grid", () => {
    const points = [
      { x: 0.1, y: 0.2 },
      { x: 0.4, y: 0.2 },
      { x: 0.4, y: 0.7 },
      { x: 0.1, y: 0.7 },
      { x: 0.1, y: 0.2 },
    ];
    expect(polygonPixelArea(points, 1000, 800)).toBeCloseTo(120_000, 6);
  });
});
