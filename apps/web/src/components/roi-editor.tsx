import {
  ArrowLeft,
  ArrowRight,
  Check,
  Hand,
  MousePointer2,
  Pentagon,
  Pencil,
  Plus,
  RotateCcw,
  ScanLine,
  Square,
  Trash2,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import {
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import { Button } from "@/components/ui/button";
import {
  ErrorState,
  LoadingState,
  PageHeader,
  TermHelp,
} from "@/components/product-ui";
import {
  api,
  type CaptureBand,
  type CaptureDetail,
  type CaptureSummary,
  type ROI,
  type ROIGeometryType,
  type ROIInput,
} from "@/lib/api";
import {
  canvasToNormalized,
  constrainImagePan,
  displayedImageBox,
  normalizedToCanvas,
  polygonPixelArea,
  type ImageViewport,
  type Point,
  zoomImageAt,
} from "@/lib/roi-coordinates";

type Tool = "pan" | "rectangle" | "polygon" | "freehand" | "edit";
type DisplayMode = "rgb" | "band";
type Drag =
  | { kind: "pan"; x: number; y: number }
  | { kind: "rectangle"; start: Point }
  | { kind: "freehand" }
  | { kind: "vertex"; index: number };

const bandOrder = ["BLUE", "GREEN", "RED", "RED_EDGE", "NIR"] as const;
const bandLabels: Record<(typeof bandOrder)[number], string> = {
  BLUE: "Blue",
  GREEN: "Green",
  RED: "Red",
  RED_EDGE: "Red Edge",
  NIR: "NIR",
};

interface RGBBands {
  BLUE: CaptureBand;
  GREEN: CaptureBand;
  RED: CaptureBand;
}

function rgbBandsFor(capture: CaptureDetail | undefined): RGBBands | undefined {
  if (!capture) return undefined;
  const blue = capture.bands.find((band) => band.band_code === "BLUE");
  const green = capture.bands.find((band) => band.band_code === "GREEN");
  const red = capture.bands.find((band) => band.band_code === "RED");
  if (!blue || !green || !red) return undefined;
  if (!blue.preview_url || !green.preview_url || !red.preview_url)
    return undefined;
  return { BLUE: blue, GREEN: green, RED: red };
}

function RGBPreview({
  bands,
  onPreviewLoadError,
  style,
}: {
  bands: RGBBands;
  onPreviewLoadError: () => void;
  style: React.CSSProperties;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [error, setError] = useState<string>();

  useEffect(() => {
    let disposed = false;
    const target = canvasRef.current;
    if (!target) return;
    setError(undefined);

    async function compose(canvas: HTMLCanvasElement) {
      const blueUrl = bands.BLUE.preview_url;
      const greenUrl = bands.GREEN.preview_url;
      const redUrl = bands.RED.preview_url;
      if (!blueUrl || !greenUrl || !redUrl) {
        setError("As três bandas RGB precisam possuir preview");
        return;
      }
      const load = (url: string) =>
        new Promise<HTMLImageElement>((resolve, reject) => {
          const image = new Image();
          image.crossOrigin = "anonymous";
          image.onload = () => resolve(image);
          image.onerror = () =>
            reject(new Error("Não foi possível carregar o preview RGB"));
          image.src = url;
        });
      try {
        const [blue, green, red] = await Promise.all([
          load(blueUrl),
          load(greenUrl),
          load(redUrl),
        ]);
        if (disposed) return;
        if (
          blue.naturalWidth !== green.naturalWidth ||
          blue.naturalWidth !== red.naturalWidth ||
          blue.naturalHeight !== green.naturalHeight ||
          blue.naturalHeight !== red.naturalHeight
        ) {
          throw new Error("Os previews RGB possuem dimensões diferentes");
        }
        canvas.width = red.naturalWidth;
        canvas.height = red.naturalHeight;
        const context = canvas.getContext("2d", { willReadFrequently: true });
        if (!context) throw new Error("Canvas RGB indisponível");
        context.drawImage(red, 0, 0);
        const redPixels = context.getImageData(
          0,
          0,
          canvas.width,
          canvas.height,
        );
        context.clearRect(0, 0, canvas.width, canvas.height);
        context.drawImage(green, 0, 0);
        const greenPixels = context.getImageData(
          0,
          0,
          canvas.width,
          canvas.height,
        );
        context.clearRect(0, 0, canvas.width, canvas.height);
        context.drawImage(blue, 0, 0);
        const bluePixels = context.getImageData(
          0,
          0,
          canvas.width,
          canvas.height,
        );
        const output = context.createImageData(canvas.width, canvas.height);
        for (let index = 0; index < output.data.length; index += 4) {
          output.data[index] = redPixels.data[index] ?? 0;
          output.data[index + 1] = greenPixels.data[index] ?? 0;
          output.data[index + 2] = bluePixels.data[index] ?? 0;
          output.data[index + 3] = Math.max(
            redPixels.data[index + 3] ?? 0,
            greenPixels.data[index + 3] ?? 0,
            bluePixels.data[index + 3] ?? 0,
          );
        }
        context.putImageData(output, 0, 0);
      } catch (cause) {
        if (!disposed) {
          setError(
            cause instanceof Error ? cause.message : "Preview RGB indisponível",
          );
          onPreviewLoadError();
        }
      }
    }
    void compose(target);
    return () => {
      disposed = true;
    };
  }, [
    bands.BLUE.preview_url,
    bands.GREEN.preview_url,
    bands.RED.preview_url,
    onPreviewLoadError,
  ]);

  if (error)
    return (
      <div className="absolute inset-0 grid place-items-center px-8 text-center text-sm text-red-100">
        {error}
      </div>
    );
  return (
    <canvas
      aria-label="Composição RGB para delimitação da folha"
      className="pointer-events-none absolute select-none"
      ref={canvasRef}
      style={style}
    />
  );
}

function openPoints(coordinates: [number, number][]) {
  const points = coordinates.map(([x, y]) => ({ x, y }));
  if (
    points.length > 1 &&
    points[0]?.x === points.at(-1)?.x &&
    points[0]?.y === points.at(-1)?.y
  )
    points.pop();
  return points;
}

function coordinates(points: Point[]): [number, number][] {
  const first = points[0];
  if (!first) return [];
  return [...points, first].map(({ x, y }) => [x, y]);
}

function transformsMatch(left: number[] | null, right: number[] | null) {
  if (left === null || right === null) return left === right;
  return (
    left.length === right.length &&
    left.every((value, index) => {
      const candidate = right[index];
      return candidate !== undefined && Math.abs(value - candidate) <= 1e-9;
    })
  );
}

function gridError(reference: CaptureBand, candidate: CaptureBand) {
  if (
    reference.width !== candidate.width ||
    reference.height !== candidate.height
  )
    return "dimensões diferentes";
  if (reference.crs !== candidate.crs) return "CRS diferente";
  if (!transformsMatch(reference.transform, candidate.transform))
    return "transformação diferente";
  return candidate.compatibility_error;
}

export function ROIEditor({ captureId }: { captureId: string }) {
  const [capture, setCapture] = useState<CaptureDetail>();
  const [captures, setCaptures] = useState<CaptureSummary[]>([]);
  const [rois, setROIs] = useState<ROI[]>([]);
  const [activeROI, setActiveROI] = useState<ROI>();
  const [activeBandId, setActiveBandId] = useState<string>();
  const [referenceBandId, setReferenceBandId] = useState<string>();
  const [displayMode, setDisplayMode] = useState<DisplayMode>("rgb");
  const [name, setName] = useState("ROI da repetição");
  const [geometryType, setGeometryType] =
    useState<ROIGeometryType>("rectangle");
  const [points, setPoints] = useState<Point[]>([]);
  const [tool, setTool] = useState<Tool>("rectangle");
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [viewport, setViewport] = useState({ width: 1, height: 1 });
  const [drag, setDrag] = useState<Drag>();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();
  const viewportRef = useRef<HTMLDivElement>(null);
  const previewRefreshes = useRef(0);

  const loadROI = useCallback((roi: ROI | undefined) => {
    setActiveROI(roi);
    setName(roi?.name ?? "ROI da repetição");
    setGeometryType(roi?.current_version.geometry_type ?? "rectangle");
    setPoints(
      roi ? openPoints(roi.current_version.normalized_coordinates) : [],
    );
    if (roi) {
      setReferenceBandId(roi.current_version.reference_asset_id);
      setActiveBandId(roi.current_version.reference_asset_id);
      setDisplayMode("rgb");
      setTool("edit");
    }
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(undefined);
    try {
      const captureValue = await api.getCapture(captureId);
      const [roiValues, captureValues] = await Promise.all([
        api.listROIs(captureId),
        api.listCaptures(captureValue.dataset_id),
      ]);
      setCapture(captureValue);
      setCaptures(captureValues);
      setROIs(roiValues);
      const firstBand =
        captureValue.bands.find((band) => band.band_code === "RED") ??
        bandOrder
          .map((code) =>
            captureValue.bands.find((band) => band.band_code === code),
          )
          .find(Boolean);
      if (roiValues[0]) loadROI(roiValues[0]);
      else {
        setActiveBandId(firstBand?.id);
        setReferenceBandId(firstBand?.id);
      }
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Falha ao abrir a repetição",
      );
    } finally {
      setLoading(false);
    }
  }, [captureId, loadROI]);

  useEffect(() => void refresh(), [refresh]);
  const refreshPreviewUrls = useCallback(() => {
    if (previewRefreshes.current >= 1) return;
    previewRefreshes.current += 1;
    void api
      .getCapture(captureId)
      .then(setCapture)
      .catch(() => {
        // The original preview error remains visible if URL renewal also fails.
      });
  }, [captureId]);

  useEffect(() => {
    const interval = window.setInterval(
      () => {
        void api
          .getCapture(captureId)
          .then(setCapture)
          .catch(() => undefined);
      },
      10 * 60 * 1000,
    );
    return () => window.clearInterval(interval);
  }, [captureId]);
  useEffect(() => {
    const element = viewportRef.current;
    if (!element) return;
    const updateViewport = () => {
      const rect = element.getBoundingClientRect();
      setViewport({
        width: Math.max(1, rect.width),
        height: Math.max(1, rect.height),
      });
    };
    const observer = new ResizeObserver(([entry]) => {
      if (entry)
        setViewport({
          width: Math.max(1, entry.contentRect.width),
          height: Math.max(1, entry.contentRect.height),
        });
    });
    updateViewport();
    const frame = window.requestAnimationFrame(updateViewport);
    const delayedMeasurement = window.setTimeout(updateViewport, 100);
    // Some embedded browsers complete their first layout after React effects.
    // Re-measure briefly so the preview never remains at the 1×1 fallback size.
    const measurementInterval = window.setInterval(updateViewport, 250);
    const stopMeasurements = window.setTimeout(
      () => window.clearInterval(measurementInterval),
      10_000,
    );
    window.addEventListener("resize", updateViewport);
    observer.observe(element);
    return () => {
      observer.disconnect();
      window.cancelAnimationFrame(frame);
      window.clearTimeout(delayedMeasurement);
      window.clearInterval(measurementInterval);
      window.clearTimeout(stopMeasurements);
      window.removeEventListener("resize", updateViewport);
    };
  }, [capture]);

  const activeBand = capture?.bands.find((band) => band.id === activeBandId);
  const captureIndex = captures.findIndex((item) => item.id === captureId);
  const previousCapture =
    captureIndex > 0 ? captures[captureIndex - 1] : undefined;
  const nextCapture =
    captureIndex >= 0 && captureIndex < captures.length - 1
      ? captures[captureIndex + 1]
      : undefined;
  const allROIsComplete =
    captures.length > 0 && captures.every((item) => item.roi_count > 0);
  const firstCaptureWithoutROI = captures.find((item) => item.roi_count === 0);
  const rgbBands = rgbBandsFor(capture);
  const referenceBand = capture?.bands.find(
    (band) => band.id === referenceBandId,
  );
  const displayBand =
    displayMode === "rgb" ? (referenceBand ?? activeBand) : activeBand;
  const view: ImageViewport | undefined = displayBand
    ? {
        viewportWidth: viewport.width,
        viewportHeight: viewport.height,
        imageWidth: displayBand.width,
        imageHeight: displayBand.height,
        zoom,
        panX: pan.x,
        panY: pan.y,
      }
    : undefined;
  const box = view ? displayedImageBox(view) : undefined;
  const activeGridError =
    displayBand && referenceBand ? gridError(referenceBand, displayBand) : null;
  const propagationErrors = referenceBand
    ? (capture?.bands ?? [])
        .map((band) => ({ band, error: gridError(referenceBand, band) }))
        .filter((item) => item.error)
    : [];
  const area = referenceBand
    ? polygonPixelArea(points, referenceBand.width, referenceBand.height)
    : 0;
  const approximateValid =
    referenceBand?.approximate_valid_fraction === null || !referenceBand
      ? null
      : Math.round(area * referenceBand.approximate_valid_fraction);

  const screenPoints = view
    ? points.map((point) => normalizedToCanvas(point, view))
    : [];

  function canvasPointFromEvent(event: ReactPointerEvent<SVGSVGElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }

  function pointFromEvent(
    event: ReactPointerEvent<SVGSVGElement>,
    clamp: boolean,
  ) {
    if (!view) return undefined;
    const point = canvasToNormalized(canvasPointFromEvent(event), view, clamp);
    if (!clamp && (point.x < 0 || point.x > 1 || point.y < 0 || point.y > 1))
      return undefined;
    return point;
  }

  function beginPointer(event: ReactPointerEvent<SVGSVGElement>) {
    if (!view || activeGridError) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    if (tool === "pan") {
      setDrag({ kind: "pan", x: event.clientX, y: event.clientY });
      return;
    }
    const point = pointFromEvent(event, false);
    if (!point) return;
    if (tool === "rectangle") {
      setGeometryType("rectangle");
      setPoints([point, point, point, point]);
      setDrag({ kind: "rectangle", start: point });
    } else if (tool === "polygon") {
      setGeometryType("polygon");
      setPoints((current) => [...current, point]);
    } else if (tool === "freehand") {
      setGeometryType("polygon");
      setPoints([point]);
      setDrag({ kind: "freehand" });
    }
  }

  function appendFreehandPoint(point: Point) {
    setPoints((current) => {
      const previous = current.at(-1);
      if (
        previous &&
        Math.hypot(point.x - previous.x, point.y - previous.y) < 0.0015
      )
        return current;
      return [...current, point];
    });
  }

  function movePointer(event: ReactPointerEvent<SVGSVGElement>) {
    if (!drag) return;
    if (drag.kind === "pan") {
      setPan((current) => {
        if (!view) return current;
        const constrained = constrainImagePan({
          ...view,
          panX: current.x + event.clientX - drag.x,
          panY: current.y + event.clientY - drag.y,
        });
        return { x: constrained.panX, y: constrained.panY };
      });
      setDrag({ kind: "pan", x: event.clientX, y: event.clientY });
      return;
    }
    const point = pointFromEvent(event, true);
    if (!point) return;
    if (drag.kind === "rectangle") {
      setPoints([
        drag.start,
        { x: point.x, y: drag.start.y },
        point,
        { x: drag.start.x, y: point.y },
      ]);
    } else if (drag.kind === "freehand") {
      appendFreehandPoint(point);
    } else {
      setPoints((current) =>
        current.map((value, index) => (index === drag.index ? point : value)),
      );
    }
  }

  function endPointer(event: ReactPointerEvent<SVGSVGElement>) {
    if (drag?.kind === "freehand") {
      const point = pointFromEvent(event, true);
      if (point) appendFreehandPoint(point);
    }
    setDrag(undefined);
  }

  function editVertex(
    event: ReactPointerEvent<SVGCircleElement>,
    index: number,
  ) {
    if (tool !== "edit") return;
    event.stopPropagation();
    event.currentTarget.parentElement?.setPointerCapture(event.pointerId);
    setDrag({ kind: "vertex", index });
  }

  function fitImage() {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }

  function changeZoom(nextZoom: number, anchor?: Point) {
    if (!view) return;
    const targetZoom = Math.min(12, Math.max(1, nextZoom));
    const zoomed = zoomImageAt(
      view,
      targetZoom,
      anchor ?? { x: viewport.width / 2, y: viewport.height / 2 },
    );
    setZoom(zoomed.zoom);
    setPan({ x: zoomed.panX, y: zoomed.panY });
  }

  function handleWheel(event: React.WheelEvent<HTMLDivElement>) {
    if (!view) return;
    event.preventDefault();
    const rect = event.currentTarget.getBoundingClientRect();
    const anchor = {
      x: event.clientX - rect.left,
      y: event.clientY - rect.top,
    };
    const multiplier = Math.exp(-event.deltaY * 0.0015);
    changeZoom(zoom * multiplier, anchor);
  }

  async function remove() {
    if (activeROI) {
      if (
        !window.confirm(
          "Excluir esta ROI? O histórico de versões será preservado.",
        )
      )
        return;
      await api.deleteROI(activeROI.id);
      const remaining = rois.filter((roi) => roi.id !== activeROI.id);
      setROIs(remaining);
      loadROI(remaining[0]);
    } else setPoints([]);
  }

  async function confirm() {
    if (!referenceBand || points.length < 3 || propagationErrors.length) return;
    setSaving(true);
    setError(undefined);
    const payload: ROIInput = {
      name,
      geometry_type: geometryType,
      normalized_coordinates: coordinates(points),
      reference_asset_id: referenceBand.id,
    };
    if (activeROI)
      payload.expected_version = activeROI.current_version.version_number;
    try {
      const saved = activeROI
        ? await api.updateROI(activeROI.id, payload)
        : await api.createROI(captureId, payload);
      setROIs((current) => [
        saved,
        ...current.filter((roi) => roi.id !== saved.id),
      ]);
      setCaptures((current) =>
        current.map((item) =>
          item.id === captureId
            ? { ...item, roi_count: Math.max(1, item.roi_count) }
            : item,
        ),
      );
      loadROI(saved);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Não foi possível salvar a ROI",
      );
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <LoadingState label="Carregando visualizador de ROI" />;
  if (!capture)
    return <ErrorState message={error ?? "Captura não encontrada"} />;

  return (
    <div className="space-y-5">
      <PageHeader
        description={
          <>
            Captura {capture.capture_key} · desenhe a <TermHelp term="ROI" />{" "}
            sobre RGB e confira o mesmo contorno em cada banda.
          </>
        }
        eyebrow="Editor de ROI"
        title={capture.replicate_label ?? "Repetição não rotulada"}
        action={
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button
              aria-label="Repetição anterior"
              disabled={!previousCapture}
              onClick={() => {
                if (previousCapture)
                  window.location.assign(`/captures/${previousCapture.id}/roi`);
              }}
              variant="outline"
            >
              <ArrowLeft /> Anterior
            </Button>
            <select
              aria-label="Mudar repetição"
              className="border-input min-w-44 rounded-md border px-3 py-2 text-sm"
              onChange={(event) =>
                window.location.assign(`/captures/${event.target.value}/roi`)
              }
              value={captureId}
            >
              {captures.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.replicate_label ?? item.capture_key}
                  {item.roi_count > 0 ? " · ROI pronta" : " · sem ROI"}
                </option>
              ))}
            </select>
            <Button
              disabled={!nextCapture}
              onClick={() => {
                if (nextCapture)
                  window.location.assign(`/captures/${nextCapture.id}/roi`);
              }}
              variant="outline"
            >
              Próxima <ArrowRight />
            </Button>
            {allROIsComplete && (
              <Button
                onClick={() =>
                  window.location.assign(
                    `/datasets/${capture.dataset_id}/processing`,
                  )
                }
              >
                <ScanLine /> Processar índices
              </Button>
            )}
            <select
              aria-label="ROI ativa"
              className="border-input min-w-52 rounded-md border px-3 py-2 text-sm"
              onChange={(event) =>
                loadROI(rois.find((roi) => roi.id === event.target.value))
              }
              value={activeROI?.id ?? "new"}
            >
              <option value="new">Nova ROI</option>
              {rois.map((roi) => (
                <option key={roi.id} value={roi.id}>
                  {roi.name} · v{roi.current_version.version_number}
                </option>
              ))}
            </select>
            <Button
              onClick={() => {
                loadROI(undefined);
                setReferenceBandId(
                  capture.bands.find((band) => band.band_code === "RED")?.id ??
                    activeBand?.id,
                );
                setDisplayMode("rgb");
              }}
              variant="outline"
            >
              <Plus /> Nova
            </Button>
          </div>
        }
      />

      <div className="surface overflow-hidden">
        <div className="border-border flex flex-wrap items-center gap-2 border-b p-3">
          <span className="text-muted-foreground mr-1 text-xs font-semibold tracking-wide uppercase">
            Ferramentas
          </span>
          <ToolButton
            active={tool === "pan"}
            onClick={() => setTool("pan")}
            title="Arrastar a imagem sem alterar a ROI"
          >
            <Hand /> Pan
          </ToolButton>
          <ToolButton
            active={tool === "rectangle"}
            onClick={() => {
              setTool("rectangle");
              setPoints([]);
            }}
            title="Desenhar uma seleção retangular"
          >
            <Square /> Retangular
          </ToolButton>
          <ToolButton
            active={tool === "polygon"}
            onClick={() => {
              setTool("polygon");
              setPoints([]);
            }}
            title="Clique para adicionar pontos ao contorno"
          >
            <Pentagon /> Por pontos
          </ToolButton>
          <ToolButton
            active={tool === "freehand"}
            onClick={() => {
              setTool("freehand");
              setPoints([]);
            }}
            title="Arraste para contornar a folha à mão livre"
          >
            <Pencil /> Mão livre
          </ToolButton>
          <ToolButton
            active={tool === "edit"}
            onClick={() => setTool("edit")}
            title="Mover os vértices já desenhados"
          >
            <MousePointer2 /> Editar vértices
          </ToolButton>
          <span className="bg-border mx-1 h-7 w-px" />
          <Button
            aria-label="Diminuir zoom"
            className="size-10 px-0"
            disabled={zoom <= 1}
            onClick={() => changeZoom(zoom / 1.25)}
            variant="outline"
          >
            <ZoomOut />
          </Button>
          <span className="w-14 text-center text-sm">
            {Math.round(zoom * 100)}%
          </span>
          <Button
            aria-label="Aumentar zoom"
            className="size-10 px-0"
            disabled={zoom >= 12}
            onClick={() => changeZoom(zoom * 1.25)}
            variant="outline"
          >
            <ZoomIn />
          </Button>
          <Button className="ml-auto" onClick={fitImage} variant="ghost">
            <RotateCcw /> Ajustar imagem
          </Button>
          <Button
            disabled={points.length === 0}
            onClick={() => setPoints([])}
            variant="ghost"
          >
            <Trash2 /> Limpar contorno
          </Button>
        </div>

        <div className="border-border bg-muted/30 flex items-end gap-1 overflow-x-auto border-b px-3 pt-3">
          <span className="text-muted-foreground mr-2 mb-2 text-xs font-semibold tracking-wide uppercase">
            Visualizar
          </span>
          <button
            className={`rounded-t-md border px-4 py-2 text-sm ${
              displayMode === "rgb"
                ? "border-primary bg-primary text-primary-foreground"
                : "border-border bg-background"
            } ${!rgbBands ? "cursor-not-allowed opacity-40" : ""}`}
            disabled={!rgbBands}
            onClick={() => setDisplayMode("rgb")}
            title="Composição RGB usada para desenhar a ROI"
            type="button"
          >
            RGB · desenhar
          </button>
          {bandOrder.map((code) => {
            const band = capture.bands.find((item) => item.band_code === code);
            const compatibilityMessage =
              band && referenceBand ? gridError(referenceBand, band) : null;
            const incompatible = Boolean(compatibilityMessage);
            return (
              <button
                className={`rounded-t-md border px-4 py-2 text-sm ${
                  band?.id === activeBandId
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border bg-background"
                } ${!band ? "cursor-not-allowed opacity-40" : ""}`}
                disabled={!band}
                key={code}
                onClick={() => {
                  if (!band) return;
                  setActiveBandId(band.id);
                  setDisplayMode("band");
                }}
                title={compatibilityMessage ?? undefined}
                type="button"
              >
                {bandLabels[code]}
                {incompatible ? " ⚠" : ""}
              </button>
            );
          })}
        </div>

        <div
          className={`relative h-[min(640px,72vh)] min-h-[480px] overflow-hidden bg-neutral-950 ${
            tool === "pan" ? "cursor-grab" : "cursor-crosshair"
          }`}
          onWheel={handleWheel}
          ref={viewportRef}
        >
          {box && displayMode === "rgb" && rgbBands ? (
            <RGBPreview
              bands={rgbBands}
              onPreviewLoadError={refreshPreviewUrls}
              style={{
                left: box.left,
                top: box.top,
                width: box.width,
                height: box.height,
              }}
            />
          ) : activeBand?.preview_url && box ? (
            <img
              alt={`Preview da banda ${activeBand.band_code}`}
              className="pointer-events-none absolute select-none"
              draggable={false}
              onError={refreshPreviewUrls}
              src={activeBand.preview_url}
              style={{
                left: box.left,
                top: box.top,
                width: box.width,
                height: box.height,
              }}
            />
          ) : (
            <div className="absolute inset-0 grid place-items-center px-8 text-center text-sm text-neutral-300">
              Esta banda ainda não possui preview derivado. Reprocesse a
              ingestão para gerar a visualização.
            </div>
          )}
          {box && (
            <svg
              aria-label="Área de seleção da ROI"
              className="absolute inset-0 size-full touch-none"
              onPointerCancel={endPointer}
              onPointerDown={beginPointer}
              onPointerMove={movePointer}
              onPointerUp={endPointer}
              viewBox={`0 0 ${String(viewport.width)} ${String(viewport.height)}`}
            >
              <rect
                fill="transparent"
                height={viewport.height}
                pointerEvents="all"
                width={viewport.width}
              />
              {!activeGridError && screenPoints.length >= 3 && (
                <>
                  <polygon
                    fill="rgba(250, 204, 21, 0.16)"
                    pointerEvents="none"
                    points={screenPoints
                      .map((point) => `${String(point.x)},${String(point.y)}`)
                      .join(" ")}
                    stroke="rgba(0, 0, 0, 0.9)"
                    strokeLinejoin="round"
                    strokeWidth="6"
                  />
                  <polygon
                    fill="none"
                    pointerEvents="none"
                    points={screenPoints
                      .map((point) => `${String(point.x)},${String(point.y)}`)
                      .join(" ")}
                    stroke="#fde047"
                    strokeLinejoin="round"
                    strokeWidth="2"
                  />
                </>
              )}
              {!activeGridError && screenPoints.length === 2 && (
                <line
                  pointerEvents="none"
                  stroke="#fde047"
                  strokeWidth="2"
                  x1={screenPoints[0]?.x}
                  x2={screenPoints[1]?.x}
                  y1={screenPoints[0]?.y}
                  y2={screenPoints[1]?.y}
                />
              )}
              {!activeGridError &&
                (tool === "edit" || tool === "polygon") &&
                screenPoints.map((point, index) => (
                  <circle
                    className={tool === "edit" ? "cursor-move" : undefined}
                    cx={point.x}
                    cy={point.y}
                    fill="#fde047"
                    key={`${String(index)}-${String(point.x)}-${String(point.y)}`}
                    onPointerDown={(event) => editVertex(event, index)}
                    pointerEvents={tool === "edit" ? "all" : "none"}
                    r="6"
                    stroke="black"
                    strokeWidth="2"
                  />
                ))}
            </svg>
          )}
          {!activeGridError && box && (
            <div className="pointer-events-none absolute right-3 bottom-3 rounded-md bg-black/75 px-3 py-2 text-xs text-white shadow-lg">
              Zoom focalizado no cursor · use Pan para reposicionar
            </div>
          )}
          {activeGridError && (
            <div className="absolute inset-x-6 bottom-6 rounded-md bg-red-950/95 p-4 text-sm text-red-100">
              Overlay bloqueado: {activeGridError}. Nenhuma propagação foi
              aplicada.
            </div>
          )}
        </div>
      </div>

      <div className="grid gap-5 lg:grid-cols-[1fr_320px]">
        <section className="surface p-5">
          <h2 className="font-semibold">Confirmar seleção</h2>
          <p className="text-muted-foreground mt-1 text-sm">
            A ROI confirmada será aplicada apenas às bandas compatíveis desta
            repetição.
          </p>
          <label className="mt-5 grid max-w-md gap-2 text-sm font-medium">
            Nome da ROI
            <input
              className="border-input rounded-md border px-3 py-2"
              maxLength={160}
              onChange={(event) => setName(event.target.value)}
              value={name}
            />
          </label>
          <div className="mt-5 space-y-3 text-sm">
            <label className="flex items-start gap-3">
              <input checked readOnly type="radio" />
              <span>
                <strong>Aplicar esta ROI apenas nesta repetição</strong>
                <small className="text-muted-foreground block">
                  A geometria será compartilhada somente pelas bandas
                  compatíveis desta captura.
                </small>
              </span>
            </label>
            <label className="flex items-start gap-3 opacity-50">
              <input disabled type="radio" />
              <span>
                Aplicar ROI equivalente a outras repetições
                <small className="block">
                  Futuro — nenhuma transformação automática é executada.
                </small>
              </span>
            </label>
          </div>
          {propagationErrors.length > 0 && (
            <div className="border-destructive/40 text-destructive mt-5 rounded-md border p-3 text-sm">
              Confirmação bloqueada. Corrija a associação/alinhamento:{" "}
              {propagationErrors
                .map(
                  ({ band, error: message }) =>
                    `${band.band_code}: ${String(message)}`,
                )
                .join("; ")}
            </div>
          )}
          {error && (
            <div className="mt-4">
              <ErrorState message={error} />
            </div>
          )}
          <div className="mt-6 flex flex-wrap gap-2">
            <Button
              disabled={
                saving ||
                points.length < 3 ||
                !name.trim() ||
                propagationErrors.length > 0
              }
              onClick={() => void confirm()}
            >
              <Check /> {saving ? "Confirmando…" : "Confirmar ROI"}
            </Button>
            <Button
              onClick={() => void remove()}
              variant={activeROI ? "destructive" : "outline"}
            >
              <Trash2 /> {activeROI ? "Excluir ROI" : "Limpar desenho"}
            </Button>
            {nextCapture ? (
              <Button
                disabled={!activeROI}
                onClick={() =>
                  window.location.assign(`/captures/${nextCapture.id}/roi`)
                }
                variant="outline"
              >
                Próxima repetição <ArrowRight />
              </Button>
            ) : allROIsComplete ? (
              <Button
                onClick={() =>
                  window.location.assign(
                    `/datasets/${capture.dataset_id}/processing`,
                  )
                }
              >
                <ScanLine /> Ir para processamento
              </Button>
            ) : firstCaptureWithoutROI ? (
              <Button
                onClick={() =>
                  window.location.assign(
                    `/captures/${firstCaptureWithoutROI.id}/roi`,
                  )
                }
                variant="outline"
              >
                Revisar repetição sem ROI <ArrowRight />
              </Button>
            ) : null}
          </div>
        </section>

        <aside className="surface p-5 text-sm">
          <h2 className="font-semibold">Precisão da seleção</h2>
          <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3">
            <dt className="text-muted-foreground">Referência</dt>
            <dd>{referenceBand?.band_code ?? "—"}</dd>
            <dt className="text-muted-foreground">Resolução original</dt>
            <dd>
              {referenceBand
                ? `${String(referenceBand.width)} × ${String(referenceBand.height)}`
                : "—"}
            </dd>
            <dt className="text-muted-foreground">Área</dt>
            <dd>{Math.round(area).toLocaleString("pt-BR")} px</dd>
            <dt className="text-muted-foreground">
              <TermHelp label="Pixels válidos ≈" term="NoData" />
            </dt>
            <dd>
              {approximateValid?.toLocaleString("pt-BR") ?? "indisponível"}
            </dd>
            <dt className="text-muted-foreground">CRS</dt>
            <dd className="truncate" title={referenceBand?.crs ?? undefined}>
              {referenceBand?.crs ?? "Não informado"}
            </dd>
          </dl>
          {activeBand && activeBand.id !== referenceBand?.id && (
            <Button
              className="mt-5 w-full"
              disabled={Boolean(activeGridError)}
              onClick={() => setReferenceBandId(activeBand.id)}
              variant="outline"
            >
              Usar {activeBand.band_code} como referência
            </Button>
          )}
          <div className="bg-muted mt-5 rounded-lg p-3 text-xs leading-5">
            A composição RGB é usada para desenhar a folha. Coordenadas são
            calculadas contra a grade raster original; o tamanho CSS do preview
            não é persistido.
          </div>
        </aside>
      </div>
    </div>
  );
}

function ToolButton({
  active,
  children,
  onClick,
  title,
}: {
  active: boolean;
  children: React.ReactNode;
  onClick: () => void;
  title?: string;
}) {
  return (
    <Button
      aria-pressed={active}
      onClick={onClick}
      title={title}
      variant={active ? "default" : "outline"}
    >
      {children}
    </Button>
  );
}
