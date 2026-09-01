export interface Point {
  x: number;
  y: number;
}

export interface ImageViewport {
  viewportWidth: number;
  viewportHeight: number;
  imageWidth: number;
  imageHeight: number;
  zoom: number;
  panX: number;
  panY: number;
}

export interface DisplayedImageBox {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** The real rendered bounds of the interactive SVG/image overlay. */
export interface RenderedBounds {
  left: number;
  top: number;
  width: number;
  height: number;
}

/**
 * Converts a browser client coordinate using the exact element that received
 * the pointer event. This avoids drift caused by layout margins, pan, or zoom.
 */
export function clientToNormalized(
  clientPoint: Point,
  bounds: RenderedBounds,
  clamp = true,
): Point {
  if (bounds.width <= 0 || bounds.height <= 0)
    throw new Error("Rendered ROI bounds must be positive");
  const normalized = {
    x: (clientPoint.x - bounds.left) / bounds.width,
    y: (clientPoint.y - bounds.top) / bounds.height,
  };
  if (!clamp) return normalized;
  return {
    x: Math.min(1, Math.max(0, normalized.x)),
    y: Math.min(1, Math.max(0, normalized.y)),
  };
}

export function displayedImageBox(view: ImageViewport): DisplayedImageBox {
  const fit = Math.min(
    view.viewportWidth / view.imageWidth,
    view.viewportHeight / view.imageHeight,
  );
  const width = view.imageWidth * fit * view.zoom;
  const height = view.imageHeight * fit * view.zoom;
  return {
    left: (view.viewportWidth - width) / 2 + view.panX,
    top: (view.viewportHeight - height) / 2 + view.panY,
    width,
    height,
  };
}

/** Keeps the zoomed image covering the viewport while still allowing panning. */
export function constrainImagePan(view: ImageViewport): ImageViewport {
  const centered = displayedImageBox({ ...view, panX: 0, panY: 0 });
  const maxPanX = Math.max(0, (centered.width - view.viewportWidth) / 2);
  const maxPanY = Math.max(0, (centered.height - view.viewportHeight) / 2);
  return {
    ...view,
    panX: maxPanX === 0 ? 0 : Math.min(maxPanX, Math.max(-maxPanX, view.panX)),
    panY: maxPanY === 0 ? 0 : Math.min(maxPanY, Math.max(-maxPanY, view.panY)),
  };
}

/**
 * Changes zoom without moving the original-image pixel below the anchor.
 * The returned pan is constrained so blank space cannot replace the image.
 */
export function zoomImageAt(
  view: ImageViewport,
  nextZoom: number,
  anchor: Point,
): ImageViewport {
  const currentBox = displayedImageBox(view);
  const imagePoint = {
    x: (anchor.x - currentBox.left) / currentBox.width,
    y: (anchor.y - currentBox.top) / currentBox.height,
  };
  const nextView = { ...view, zoom: nextZoom, panX: 0, panY: 0 };
  const centeredBox = displayedImageBox(nextView);
  nextView.panX =
    anchor.x - imagePoint.x * centeredBox.width - centeredBox.left;
  nextView.panY =
    anchor.y - imagePoint.y * centeredBox.height - centeredBox.top;
  return constrainImagePan(nextView);
}

export function canvasToNormalized(
  canvasPoint: Point,
  view: ImageViewport,
  clamp = true,
): Point {
  const box = displayedImageBox(view);
  const normalized = {
    x: (canvasPoint.x - box.left) / box.width,
    y: (canvasPoint.y - box.top) / box.height,
  };
  if (!clamp) return normalized;
  return {
    x: Math.min(1, Math.max(0, normalized.x)),
    y: Math.min(1, Math.max(0, normalized.y)),
  };
}

export function normalizedToCanvas(point: Point, view: ImageViewport): Point {
  const box = displayedImageBox(view);
  return {
    x: box.left + point.x * box.width,
    y: box.top + point.y * box.height,
  };
}

export function normalizedToImage(
  point: Point,
  imageWidth: number,
  imageHeight: number,
): Point {
  return { x: point.x * imageWidth, y: point.y * imageHeight };
}

export function imageToNormalized(
  point: Point,
  imageWidth: number,
  imageHeight: number,
): Point {
  return { x: point.x / imageWidth, y: point.y / imageHeight };
}

export function polygonPixelArea(
  points: Point[],
  imageWidth: number,
  imageHeight: number,
) {
  if (points.length < 3) return 0;
  const first = points[0];
  if (!first) return 0;
  const closed =
    first.x === points.at(-1)?.x && first.y === points.at(-1)?.y
      ? points
      : [...points, first];
  return (
    Math.abs(
      closed.slice(0, -1).reduce((sum, point, index) => {
        const next = closed[index + 1];
        if (!next) return sum;
        return (
          sum +
          point.x * imageWidth * (next.y * imageHeight) -
          next.x * imageWidth * (point.y * imageHeight)
        );
      }, 0),
    ) / 2
  );
}
