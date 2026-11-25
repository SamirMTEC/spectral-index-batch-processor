import { type PointerEvent, useCallback, useEffect, useRef, useState } from "react";

import { UploadPanel } from "@/components/upload-panel";
import { Button } from "@/components/ui/button";
import { WorkflowStepper } from "@/components/product-ui";
import {
  api, type CalibrationBand, type CalibrationPanel,
  type CalibrationROI, type CalibrationRun, type CalibrationRunInput,
  type CaptureSummary,
} from "@/lib/api";

const steps = [
  "Importar imagens", "Identificar bandas", "Selecionar ROIs",
  "Configurar calibração", "Executar e auditar", "Exportar resultados",
];
const spectralBands = ["BLUE", "GREEN", "RED", "RED_EDGE", "NIR"];
const defaultSurfaces = ["Preto", "Cinza-escuro", "Cinza-claro", "Branco"];
const mapirProfile: Record<string, Record<string, string>> = {
  "Preto": { BLUE: "0.01997", GREEN: "0.01956", RED: "0.01925", RED_EDGE: "0.01949", NIR: "0.02178" },
  "Cinza-escuro": { BLUE: "0.18559", GREEN: "0.19738", RED: "0.20264", RED_EDGE: "0.21749", NIR: "0.23209" },
  "Cinza-claro": { BLUE: "0.25095", GREEN: "0.26648", RED: "0.26687", RED_EDGE: "0.26680", NIR: "0.28000" },
  "Branco": { BLUE: "0.79713", GREEN: "0.87191", RED: "0.87697", RED_EDGE: "0.87546", NIR: "0.86677" },
};
const desktopLocal = import.meta.env.VITE_DESKTOP_LOCAL === "true";

function message(error: unknown) {
  return error instanceof Error ? error.message : "Ocorreu um erro inesperado.";
}

function formatted(value: number | null | undefined, digits = 4) {
  return value == null ? "—" : value.toFixed(digits);
}

function metadataText(value: unknown) {
  return typeof value === "string" || typeof value === "number" ? String(value) : "—";
}

function optionalLimit(raw: string) {
  if (!raw.trim()) return null;
  const value = Number(raw.replace(",", "."));
  if (!Number.isFinite(value) || value < 0) throw new Error("Limite de auditoria inválido.");
  return value;
}

function svgPoint(x: number, y: number) {
  return `${String(x * 1000)},${String(y * 1000)}`;
}

function AuditGraph({ band, internalCheck = false }: { band: NonNullable<CalibrationRun["result"]>["bands"][string]; internalCheck?: boolean }) {
  const points = band.surfaces;
  return (
    <div className="rounded-lg border p-4">
      <strong className="text-sm">Referência × estimativa</strong>
      <svg aria-label="Gráfico de calibração e linha 1 para 1" className="mt-2 w-full max-w-md" viewBox="0 0 300 260">
        <line stroke="#9ca3af" strokeDasharray="4 4" x1="30" x2="250" y1="230" y2="10" />
        <line stroke="#24332d" x1="30" x2="250" y1="230" y2="230" />
        <line stroke="#24332d" x1="30" x2="30" y1="230" y2="10" />
        {points.map((point) => (
          <circle
            cx={30 + point.reference * 220}
            cy={230 - point.estimated * 220}
            fill={point.role === "validation" ? "#d97706" : "#00674f"}
            key={`${point.surface}-${point.role}`}
            r="5"
          ><title>{`${point.surface}: referência ${formatted(point.reference)}, estimativa ${formatted(point.estimated)}, resíduo ${formatted(point.residual)}`}</title></circle>
        ))}
        <text fontSize="11" x="90" y="253">Reflectância de referência</text>
        <text fontSize="11" transform="translate(12 170) rotate(-90)">Reflectância estimada</text>
      </svg>
      <p className="text-muted-foreground text-xs">Linha tracejada: 1:1. Verde: ajuste. Laranja: {internalCheck ? "checagem interna do MAPIR" : "validação independente"}.</p>
    </div>
  );
}

function Histogram({ counts, label }: { counts: number[]; label: string }) {
  const maximum = Math.max(1, ...counts);
  return <div><strong className="text-xs">{label}</strong><svg aria-label={`Histograma ${label}`} className="mt-1 h-24 w-full" preserveAspectRatio="none" viewBox="0 0 320 100">{counts.map((count, index) => <rect fill="#08775b" height={count / maximum * 90} key={`${String(index)}-${String(count)}`} width="8" x={index * 10} y={100 - count / maximum * 90} />)}</svg></div>;
}

function CalibrationResults({ run, activeBand, onBandChange }: {
  run: CalibrationRun;
  activeBand: string;
  onBandChange: (band: string) => void;
}) {
  const result = run.result;
  if (!result) return null;
  const bandName = result.bands[activeBand] ? activeBand : (Object.keys(result.bands)[0] ?? "");
  const selected = bandName ? result.bands[bandName] : undefined;
  const previews = bandName ? result.preview_urls?.[bandName] : undefined;
  return <div className="space-y-5">
    <p className="rounded-md bg-amber-50 p-3 text-sm">Classificação operacional: <strong>{result.classification}</strong>. {result.classification_reasons.join(" ")}</p>
    {result.reference_captures && <section className="rounded-md border p-4 text-sm"><h3 className="font-semibold">Capturas de referência processadas</h3><div className="mt-2 overflow-x-auto"><table className="w-full text-left text-xs"><thead><tr className="border-b"><th className="p-2">Referência</th><th className="p-2">Banda</th><th className="p-2">Alvo</th><th className="p-2">Fator K / inclinação</th></tr></thead><tbody>{result.reference_captures.flatMap((reference) => Object.entries(reference.bands).map(([band, detail]) => <tr className="border-b" key={`${reference.capture_id}-${band}`}><td className="p-2">{reference.capture_key}</td><td className="p-2">{band}</td><td className="p-2">{detail.surfaces.find((item) => item.role === "fit")?.surface ?? "—"}</td><td className="p-2">{formatted(detail.model.slope, 7)}</td></tr>))}</tbody></table></div></section>}
    {result.combined_models && <section className="rounded-md border border-emerald-300 bg-emerald-50 p-4 text-sm"><h3 className="font-semibold">Calibração combinada aplicada a todas as repetições</h3><p>Em cada banda, o programa calcula um coeficiente na ROI própria de cada imagem de referência e aplica a média desses coeficientes a todas as imagens, inclusive às referências. As coordenadas das ROIs não são compartilhadas entre pastas.</p><p className="mt-1">Referências: {result.reference_captures?.map((reference) => reference.capture_key).join(" + ")}.</p></section>}
    {result.combined_reference_check && <section className="rounded-md border p-4 text-sm"><h3 className="font-semibold">Conferência das referências após combinação</h3><p className="mb-2 text-xs">Os resíduos abaixo usam o coeficiente realmente aplicado às imagens. RMSE calculado sobre as imagens de referência; não substitui validação independente.</p><div className="overflow-x-auto"><table className="w-full text-left text-xs"><thead><tr><th>Banda</th><th>RMSE no painel</th><th>Resíduos por imagem</th></tr></thead><tbody>{Object.entries(result.combined_reference_check).map(([band, check]) => <tr className="border-t" key={band}><td className="p-2">{band}</td><td className="p-2">{formatted(check.rmse, 5)}</td><td className="p-2">{check.observations.map((item) => `${item.capture_key}: ${formatted(item.residual, 5)}`).join(" · ")}</td></tr>)}</tbody></table></div></section>}
    {result.target_summary && <section className="rounded-md border p-4 text-sm"><h3 className="font-semibold">Capturas de plantas calibradas</h3><p>{result.target_summary.completed} de {result.target_summary.requested} concluídas · {result.target_summary.failed} falhas · {result.target_summary.skipped ?? 0} pendentes.</p><ul className="mt-2 space-y-1">{result.targets?.map((target) => <li key={target.capture_id}>{target.capture_key}: <strong>{target.state === "completed" ? "concluída" : "falhou"}</strong> · {result.combined_models ? "referências combinadas" : `referência ${result.reference_captures?.find((reference) => reference.capture_id === target.reference_capture_id)?.capture_key ?? target.reference_capture_id ?? "principal"}`}{target.errors.length > 0 && ` — ${target.errors.join("; ")}`}</li>)}</ul></section>}
    {result.capture_preview_urls && <section className="space-y-3 rounded-md border p-4"><h3 className="font-semibold">Imagens calibradas por repetição</h3>{[...(result.reference_captures ?? []), ...(result.targets ?? []).filter((target) => target.state === "completed")].map((capture) => <details className="rounded-md border p-3" key={capture.capture_id}><summary className="cursor-pointer font-semibold">{capture.capture_key} · {Object.keys(capture.bands).length} bandas calibradas</summary><div className="mt-3 grid gap-4 md:grid-cols-2 xl:grid-cols-3">{Object.entries(capture.bands).map(([band, detail]) => { const images = result.capture_preview_urls?.[capture.capture_id]?.[band]; const original = "original_filename" in detail ? detail.original_filename : detail.filename; return <figure className="rounded-md border p-2" key={band}><figcaption className="mb-2 text-sm font-semibold">{original ?? capture.capture_key} · {band}</figcaption>{images?.calibrated ? <img alt={`Imagem calibrada ${original ?? capture.capture_key}, banda ${band}`} className="w-full" src={images.calibrated} /> : <p>Prévia indisponível</p>}<p className="mt-1 break-all text-xs">Saída: {detail.output ?? "—"}</p></figure>; })}</div></details>)}</section>}
    <ul className="list-disc pl-5 text-sm">{result.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
    {selected && !result.capture_preview_urls && <section className="space-y-3 rounded-md border p-4">
      <h3 className="font-semibold">{result.target_summary ? "Painel: original | calibrada" : "Original | Calibrada"}</h3>
      <select className="rounded-md border p-2 text-sm" onChange={(event) => onBandChange(event.target.value)} value={bandName}>{Object.keys(result.bands).map((name) => <option key={name}>{name}</option>)}</select>
      <div className="grid gap-4 md:grid-cols-2"><figure><figcaption className="mb-2 text-sm font-semibold">DN original · {bandName}</figcaption>{previews?.original ? <img alt={`DN original da banda ${bandName}`} className="w-full rounded-md border" src={previews.original} /> : <p className="text-muted-foreground text-sm">Prévia indisponível</p>}</figure><figure><figcaption className="mb-2 text-sm font-semibold">Reflectância estimada · {bandName}</figcaption>{previews?.calibrated ? <img alt={`Reflectância estimada da banda ${bandName}`} className="w-full rounded-md border" src={previews.calibrated} /> : <p className="text-muted-foreground text-sm">Prévia indisponível</p>}</figure></div>
      <p className="text-muted-foreground text-xs">Cada prévia usa contraste próprio somente para exibição. DN e reflectância têm unidades diferentes; uma subtração direta entre as imagens não constitui mapa físico de diferença.</p>
      <div className="grid gap-4 sm:grid-cols-3 text-sm"><p>Pixels inválidos: {formatted(selected.distribution.invalid_percent, 2)}%</p><p>Saturados: {formatted(selected.distribution.saturated_percent, 2)}%</p><p>Estimativas fora de [0,1]: {formatted(selected.distribution.out_of_range_percent, 2)}%</p></div>
      <div className="grid gap-4 md:grid-cols-2"><Histogram counts={selected.distribution.histogram_before.counts} label={`Entrada: ${run.input_mode}`} /><Histogram counts={selected.distribution.histogram_after.counts} label="Saída: reflectância estimada" /></div>
    </section>}
    <div className="grid gap-4 lg:grid-cols-2">{Object.entries(result.bands).map(([name, band]) => <div className="rounded-md border p-4" key={name}><h3 className="text-lg font-semibold">{name}</h3><p className="text-sm">{result.combined_models ? "Equação combinada aplicada" : "Equação aplicada"}: ρ = {formatted(result.combined_models?.[name]?.slope ?? band.model.slope, 7)} × X + {formatted(result.combined_models?.[name]?.intercept ?? band.model.intercept, 7)}</p>{result.combined_models && <p className="text-xs">Coeficiente individual da primeira referência: {formatted(band.model.slope, 7)}.</p>}<p className="text-sm">Ajuste individual: RMSE {formatted(band.fit_error.rmse)}, R² {formatted(band.fit_error.r2)} · {result.target_summary ? "Checagem interna MAPIR" : "Validação"}: RMSE {formatted(result.target_summary ? band.internal_check?.rmse : band.validation?.rmse)}</p><AuditGraph band={band} internalCheck={Boolean(result.target_summary)} /><div className="mt-3 overflow-x-auto"><table className="w-full text-xs"><thead><tr><th>Superfície</th><th>DN mediano</th><th>Referência</th><th>Estimativa individual</th><th>Resíduo individual</th></tr></thead><tbody>{band.surfaces.map((item) => <tr key={item.surface}><td>{item.surface}</td><td>{formatted(item.dn.median, 1)}</td><td>{formatted(item.reference)}</td><td>{formatted(item.estimated)}</td><td>{formatted(item.residual)}</td></tr>)}</tbody></table></div></div>)}</div>
  </div>;
}

export function CalibrationPage({ datasetId }: { datasetId: string }) {
  const [step, setStep] = useState(0);
  const [captures, setCaptures] = useState<CaptureSummary[]>([]);
  const [captureId, setCaptureId] = useState("");
  const [editingCaptureId, setEditingCaptureId] = useState("");
  const [additionalReferenceIds, setAdditionalReferenceIds] = useState<string[]>([]);
  const [targetReferenceMap, setTargetReferenceMap] = useState<Record<string, string>>({});
  const [batchMode, setBatchMode] = useState(false);
  const [batchFitSurface, setBatchFitSurface] = useState("Cinza-claro");
  const [targetCaptureIds, setTargetCaptureIds] = useState<string[]>([]);
  const [sessionName, setSessionName] = useState("");
  const [sessionDate, setSessionDate] = useState("");
  const [sessionTimezone, setSessionTimezone] = useState("America/Bahia");
  const [illuminationMethod, setIlluminationMethod] = useState<"stable_panel" | "not_assessed">("not_assessed");
  const [illuminationNotes, setIlluminationNotes] = useState("");
  const [bandsByCapture, setBandsByCapture] = useState<Record<string, CalibrationBand[]>>({});
  const [activeBand, setActiveBand] = useState("NIR");
  const [referenceBand, setReferenceBand] = useState("NIR");
  const [panels, setPanels] = useState<CalibrationPanel[]>([]);
  const [panelId, setPanelId] = useState("");
  const [runs, setRuns] = useState<CalibrationRun[]>([]);
  const [run, setRun] = useState<CalibrationRun | null>(null);
  const [roisByCapture, setRoisByCapture] = useState<Record<string, CalibrationROI[]>>({});
  const [surface, setSurface] = useState<string>("Cinza-claro");
  const [whiteAcknowledged, setWhiteAcknowledged] = useState(false);
  const [geometry, setGeometry] = useState<"rectangle" | "polygon">("rectangle");
  const [bandSpecific, setBandSpecific] = useState(false);
  const [role, setRole] = useState<"fit" | "validation">("fit");
  const [polygonPoints, setPolygonPoints] = useState<[number, number][]>([]);
  const [dragStart, setDragStart] = useState<[number, number] | null>(null);
  const [pixelMode, setPixelMode] = useState(false);
  const [pixel, setPixel] = useState<{ x: number; y: number; dn: number; camera_signal: number | null } | null>(null);
  const imageRef = useRef<HTMLImageElement>(null);
  const [zoom, setZoom] = useState(1);
  const [brightness, setBrightness] = useState(1);
  const [contrast, setContrast] = useState(1);
  const [panelName, setPanelName] = useState("");
  const [manufacturer, setManufacturer] = useState("MAPIR");
  const [model, setModel] = useState("");
  const [serial, setSerial] = useState("");
  const [sourceLabel, setSourceLabel] = useState("");
  const [certified, setCertified] = useState(false);
  const [csvText, setCsvText] = useState("");
  const [values, setValues] = useState<Record<string, Record<string, string>>>({});
  const [method, setMethod] = useState<"proportional" | "linear">("proportional");
  const [selectedBand, setSelectedBand] = useState("");
  const bandSelectionDefaulted = useRef(false);
  const [showAllSurfaces, setShowAllSurfaces] = useState(false);
  const [inputMode, setInputMode] = useState<"p4_signal" | "dn_empirical">("p4_signal");
  const [acknowledgeDN, setAcknowledgeDN] = useState(false);
  const [weighted, setWeighted] = useState(false);
  const [robust, setRobust] = useState(false);
  const [maxCv, setMaxCv] = useState("");
  const [maxSaturation, setMaxSaturation] = useState("");
  const [maxValidationRmse, setMaxValidationRmse] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const loadingBandIds = useRef<Set<string>>(new Set());

  const refresh = useCallback(async () => {
    try {
      const [newCaptures, newPanels, newRuns] = await Promise.all([
        api.listCaptures(datasetId), api.listCalibrationPanels(), api.listCalibrationRuns(datasetId),
      ]);
      setCaptures(newCaptures);
      const available = new Set(newCaptures.map((capture) => capture.id));
      setAdditionalReferenceIds((current) => current.filter((id) => available.has(id)));
      setTargetCaptureIds((current) => current.filter((id) => available.has(id)));
      setTargetReferenceMap((current) => Object.fromEntries(
        Object.entries(current).filter(([target, reference]) =>
          available.has(target) && available.has(reference),
        ),
      ));
      setPanels(newPanels);
      setRuns(newRuns);
      const firstCapture = newCaptures[0];
      setCaptureId((current) =>
        newCaptures.some((capture) => capture.id === current)
          ? current
          : (firstCapture?.id ?? ""),
      );
      setEditingCaptureId((current) =>
        newCaptures.some((capture) => capture.id === current)
          ? current : (firstCapture?.id ?? ""),
      );
    } catch (cause) { setError(message(cause)); }
  }, [datasetId]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    for (const id of [captureId, ...additionalReferenceIds]) {
      if (id && !bandsByCapture[id] && !loadingBandIds.current.has(id)) {
        loadingBandIds.current.add(id);
        void api.getCalibrationBands(id).then((loaded) =>
          setBandsByCapture((current) => ({ ...current, [id]: loaded })),
        ).catch((cause: unknown) => setError(message(cause)))
          .finally(() => loadingBandIds.current.delete(id));
      }
    }
  }, [captureId, additionalReferenceIds, bandsByCapture]);
  useEffect(() => {
    const loaded = bandsByCapture[editingCaptureId] ?? [];
    const firstBand = loaded[0];
    if (firstBand) {
      setActiveBand((current) => loaded.some((band) => band.band === current) ? current : firstBand.band);
      setReferenceBand((current) => loaded.some((band) => band.band === current) ? current : firstBand.band);
    }
  }, [editingCaptureId, bandsByCapture]);
  useEffect(() => {
    if (step === 3 && !bandSelectionDefaulted.current && activeBand) {
      bandSelectionDefaulted.current = true;
      setSelectedBand(activeBand);
    }
  }, [step, activeBand]);
  const runId = run?.id;
  const runState = run?.state;
  useEffect(() => {
    if (!runId || !["queued", "running", "cancel_requested"].includes(runState ?? "")) return;
    const timer = window.setInterval(() => {
      void api.getCalibrationRun(runId).then((updated) => {
        setRun(updated);
        if (["completed", "failed", "cancelled"].includes(updated.state)) void refresh();
      }).catch((cause: unknown) => setError(message(cause)));
    }, 2500);
    return () => window.clearInterval(timer);
  }, [runId, runState, refresh]);

  const bands = bandsByCapture[editingCaptureId] ?? [];
  const rois = roisByCapture[editingCaptureId] ?? [];
  const primaryRois = roisByCapture[captureId] ?? [];
  const referenceIds = [captureId, ...(batchMode ? additionalReferenceIds : [])].filter(Boolean);
  function setRois(update: CalibrationROI[] | ((current: CalibrationROI[]) => CalibrationROI[])) {
    setRoisByCapture((current) => {
      const previous = current[editingCaptureId] ?? [];
      return {
        ...current,
        [editingCaptureId]: typeof update === "function" ? update(previous) : update,
      };
    });
  }
  const image = bands.find((band) => band.band === activeBand);
  const displayedRois = rois.filter((roi) => roi.band === null || roi.band === activeBand);
  const primaryBands = bandsByCapture[captureId] ?? [];
  const relevantBands = selectedBand ? primaryBands.filter((band) => band.band === selectedBand) : primaryBands;
  const allP4 = relevantBands.length > 0 && relevantBands.every((band) => band.p4_signal_available);
  const selectedPanel = panels.find((panel) => panel.id === panelId);
  const bandsToCalibrate = relevantBands;
  function effectiveRoisFor(id: string): CalibrationROI[] {
    const selected = roisByCapture[id] ?? [];
    return batchMode
      ? selected.map((roi) => ({ ...roi, role: roi.surface === batchFitSurface ? "fit" : "validation" }))
      : selected;
  }
  const effectiveRois = effectiveRoisFor(editingCaptureId);
  const calibrationIssues = referenceIds.flatMap((id) => {
    const label = captures.find((item) => item.id === id)?.capture_key ?? id;
    const selectedBands = id === captureId && selectedBand
      ? (bandsByCapture[id] ?? []).filter((band) => band.band === selectedBand)
      : (bandsByCapture[id] ?? []);
    if (!selectedBands.length) return [`${label}: aguarde o carregamento das bandas.`];
    const selectedRois = effectiveRoisFor(id);
    return selectedBands.flatMap((band) => {
      const fitting = selectedRois.filter((roi) => roi.role === "fit" && (roi.band === null || roi.band === band.band));
      const matched = fitting.filter((roi) => selectedPanel?.values[roi.surface]?.[band.band] != null);
      if (matched.length >= (method === "linear" ? 2 : 1)) return [];
      const references = Object.entries(selectedPanel?.values ?? {})
        .filter(([, values]) => values[band.band] != null).map(([name]) => name);
      if (!references.length) return [`${label}/${band.band}: informe a reflectância da cor usada no painel.`];
      if (!fitting.length) return [`${label}/${band.band}: desenhe uma ROI de ajuste na cor ${references.join(" ou ")} (etapa 3).`];
      if (!matched.length) return [`${label}/${band.band}: a ROI está marcada como ${fitting.map((roi) => roi.surface).join(" ou ")}, mas a reflectância salva é de ${references.join(" ou ")}. Confira a cor da ROI na etapa 3.`];
      return [`${label}/${band.band}: a regressão linear exige duas superfícies com ROI e reflectância; para uma cor, escolha Proporcional.`];
    });
  });
  const fittingSurface = rois.find((roi) => roi.role === "fit" && (roi.band === null || roi.band === selectedBand))?.surface || surface;
  const panelSurfaces = selectedBand && !showAllSurfaces ? [fittingSurface] : defaultSurfaces;
  const whiteFit = batchMode ? batchFitSurface === "Branco" : rois.some((roi) => roi.surface === "Branco" && roi.role === "fit");

  function imagePoint(event: PointerEvent<HTMLElement>): [number, number] | null {
    const rectangle = imageRef.current?.getBoundingClientRect();
    if (!rectangle) return null;
    return [
      Math.max(0, Math.min(1, (event.clientX - rectangle.left) / rectangle.width)),
      Math.max(0, Math.min(1, (event.clientY - rectangle.top) / rectangle.height)),
    ];
  }

  function saveROI(coordinates: [number, number][]) {
    setRun(null);
    const band = bandSpecific || activeBand !== referenceBand ? activeBand : null;
    setRois((current) => [
      ...current.filter((roi) => roi.surface !== surface || roi.band !== band),
      { surface, band, coordinates, role },
    ]);
  }

  function onPointerDown(event: PointerEvent<HTMLDivElement>) {
    const point = imagePoint(event);
    if (!point) return;
    if (pixelMode && image && editingCaptureId) {
      const x = Math.min(image.width - 1, Math.floor(point[0] * image.width));
      const y = Math.min(image.height - 1, Math.floor(point[1] * image.height));
      void api.getCalibrationPixel(editingCaptureId, activeBand, x, y)
        .then(setPixel)
        .catch((cause: unknown) => setError(message(cause)));
      return;
    }
    if (geometry === "polygon") {
      setPolygonPoints((current) => [...current, point]);
    } else {
      event.currentTarget.setPointerCapture(event.pointerId);
      setDragStart(point);
    }
  }

  function onPointerUp(event: PointerEvent<HTMLDivElement>) {
    if (pixelMode) return;
    if (!dragStart) return;
    const end = imagePoint(event);
    setDragStart(null);
    if (!end) return;
    const [x0, y0] = dragStart;
    const [x1, y1] = end;
    if (Math.abs(x1 - x0) < 0.005 || Math.abs(y1 - y0) < 0.005) return;
    saveROI([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]);
  }

  async function savePanel() {
    setError(""); setBusy(true);
    try {
      const numericValues: Record<string, Record<string, number>> = {};
      if (!csvText) {
        for (const [surfaceName, bandValues] of Object.entries(values)) {
          for (const [band, raw] of Object.entries(bandValues)) {
            if (raw.trim()) {
              const value = Number(raw.replace(",", "."));
              if (!Number.isFinite(value) || value < 0 || value > 1) throw new Error(`Valor inválido: ${surfaceName}/${band}`);
              numericValues[surfaceName] ??= {};
              numericValues[surfaceName][band] = value;
            }
          }
        }
      }
      const panel = await api.createCalibrationPanel({
        name: panelName, manufacturer, model, serial_number: serial,
        source_label: sourceLabel, certified, values: numericValues,
        ...(csvText ? { certificate_csv: csvText } : {}),
      });
      setPanels((current) => [panel, ...current]);
      setPanelId(panel.id);
      setRun(null);
      setCsvText("");
    } catch (cause) { setError(message(cause)); } finally { setBusy(false); }
  }

  function runInput(execute: boolean): CalibrationRunInput {
    if (!captureId) throw new Error("Selecione a captura TIFF do painel na etapa 1.");
    if (!panelId) throw new Error("Selecione um painel de reflectâncias salvo na etapa 4.");
    if (!primaryRois.length) throw new Error("Selecione as regiões da primeira referência.");
    if (additionalReferenceIds.some((id) => !(roisByCapture[id] ?? []).length)) throw new Error("Desenhe a ROI em cada captura de referência.");
    if (!selectedPanel) throw new Error("O painel selecionado não foi encontrado.");
    if (!bandsToCalibrate.length) throw new Error("Selecione uma captura com bandas disponíveis na etapa 1.");
    if (calibrationIssues.length) throw new Error(calibrationIssues.join(" "));
    if (inputMode === "p4_signal" && !bandsToCalibrate.every((band) => band.p4_signal_available)) throw new Error("Metadados para correção DJI incompletos.");
    if (batchMode && !targetCaptureIds.length && !additionalReferenceIds.length) throw new Error("Selecione capturas para calibrar ou mais referências do painel.");
    if (batchMode && (selectedBand || inputMode !== "p4_signal")) throw new Error("A calibração em lote requer as cinco bandas e o sinal DJI corrigido.");
    if (batchMode && referenceIds.some((id) => (bandsByCapture[id] ?? []).some((band) => !band.p4_signal_available))) throw new Error("Uma referência tem metadados DJI incompletos.");
    return {
      dataset_id: datasetId, capture_id: captureId, panel_id: panelId,
      target_capture_ids: batchMode ? targetCaptureIds : [],
      additional_references: batchMode ? additionalReferenceIds.map((id) => ({ capture_id: id, rois: effectiveRoisFor(id) })) : [],
      target_reference_map: batchMode && !desktopLocal ? Object.fromEntries(targetCaptureIds.map((id) => [id, targetReferenceMap[id] || captureId])) : {},
      reference_combination: batchMode && desktopLocal ? "mean_coefficients" : "per_target",
      session_name: sessionName.trim() || null,
      session_date: sessionDate ? new Date(`${sessionDate}T12:00:00`).toISOString() : null,
      session_timezone: sessionTimezone,
      illumination_method: illuminationMethod,
      illumination_notes: illuminationNotes.trim() || null,
      method, input_mode: inputMode, rois: effectiveRoisFor(captureId), reference_band: selectedBand || referenceBand,
      selected_band: selectedBand || null,
      acknowledge_dn_limits: acknowledgeDN,
      exclude_saturated: true, edge_inset_percent: 5,
      weighted, robust, max_cv: optionalLimit(maxCv),
      max_saturation_percent: optionalLimit(maxSaturation),
      max_validation_rmse: batchMode ? null : optionalLimit(maxValidationRmse),
      execute,
    };
  }

  async function startRun(execute: boolean) {
    setError(""); setBusy(true);
    try {
      const created = await api.createCalibrationRun(runInput(execute));
      setRun(created);
      setRuns((current) => [created, ...current]);
      setStep(execute ? 4 : 3);
    } catch (cause) { setError(message(cause)); } finally { setBusy(false); }
  }

  function restore(previous: CalibrationRun) {
    setRun(previous);
    void api.getCalibrationRun(previous.id).then(setRun)
      .catch((cause: unknown) => setError(message(cause)));
    setCaptureId(previous.config.capture_id);
    setEditingCaptureId(previous.config.capture_id);
    setAdditionalReferenceIds((previous.config.additional_references ?? []).map((reference) => reference.capture_id));
    setTargetCaptureIds(previous.config.target_capture_ids ?? []);
    setTargetReferenceMap(previous.config.target_reference_map ?? {});
    setBatchMode(Boolean(previous.config.target_capture_ids?.length || previous.config.additional_references?.length));
    setSessionName(previous.config.session_name ?? "");
    setSessionDate(previous.config.session_date?.slice(0, 10) ?? "");
    setSessionTimezone(previous.config.session_timezone ?? "America/Bahia");
    setIlluminationMethod(previous.config.illumination_method ?? "not_assessed");
    setIlluminationNotes(previous.config.illumination_notes ?? "");
    setPanelId(previous.config.panel_id);
    setRoisByCapture({
      [previous.config.capture_id]: previous.config.rois,
      ...Object.fromEntries((previous.config.additional_references ?? []).map((reference) => [reference.capture_id, reference.rois])),
    });
    setBatchFitSurface(previous.config.rois.find((roi) => roi.role === "fit")?.surface ?? "Cinza-claro");
    setMethod(previous.config.method);
    setInputMode(previous.config.input_mode);
    setReferenceBand(previous.config.reference_band || "NIR");
    bandSelectionDefaulted.current = true;
    setSelectedBand(previous.config.selected_band || "");
    setMaxCv(previous.config.max_cv?.toString() ?? "");
    setMaxSaturation(previous.config.max_saturation_percent?.toString() ?? "");
    setMaxValidationRmse(previous.config.max_validation_rmse?.toString() ?? "");
    setStep(previous.state === "completed" ? 4 : 3);
  }

  async function download() {
    if (!run) return;
    try {
      const objectUrl = await api.downloadCalibrationFile(run.id);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = `calibracao-${run.id}.zip`;
      link.style.display = "none";
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 30_000);
    } catch (cause) { setError(message(cause)); }
  }

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      {desktopLocal && <WorkflowStepper current={3} datasetId={datasetId} />}
      <header className="space-y-2">
        <p className="text-sm font-semibold tracking-wide text-emerald-800 uppercase">Fluxo científico</p>
        <h1 className="text-3xl font-bold">Calibração Radiométrica Multiespectral</h1>
        <p className="text-muted-foreground">Calibração por painel com equações, procedência e auditoria separada de ajuste e validação.</p>
      </header>
      <nav aria-label="Etapas da calibração" className="grid gap-2 sm:grid-cols-3 lg:grid-cols-6">
        {steps.map((name, index) => (
          <button aria-current={step === index ? "step" : undefined} className={`rounded-lg border px-3 py-3 text-left text-sm ${step === index ? "border-emerald-700 bg-emerald-50 font-semibold" : "bg-white"}`} key={name} onClick={() => setStep(index)} type="button">
            <span className="mr-2 text-emerald-800">{index + 1}.</span>{name}
          </button>
        ))}
      </nav>
      {error && <div className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-900" role="alert">{error}</div>}

      {step === 0 && <section className="space-y-5">
        <div className="rounded-lg border bg-white p-5">
          <h2 className="text-xl font-semibold">1. Importar imagens</h2>
          <p className="text-muted-foreground mt-2 text-sm">Use os TIFFs originais das cinco bandas. O JPG é apenas referência visual. Os originais permanecem imutáveis.</p>
        </div>
        <UploadPanel datasetId={datasetId} onImagesChanged={() => { setBandsByCapture({}); setRun(null); void refresh(); }} />
        <div className="rounded-lg border bg-white p-5">
          <div className="flex items-center justify-between gap-2"><h3 className="font-semibold">Capturas disponíveis</h3><Button onClick={() => void refresh()} variant="outline">Atualizar capturas</Button></div>
          <fieldset className="mt-3 flex gap-5 text-sm"><legend className="font-medium">Tipo de sessão</legend><label><input checked={!batchMode} onChange={() => { setBatchMode(false); setEditingCaptureId(captureId); setRun(null); }} type="radio" /> Somente captura do painel</label><label><input checked={batchMode} onChange={() => { setBatchMode(true); setEditingCaptureId(captureId); setSelectedBand(""); bandSelectionDefaulted.current = true; setMethod("proportional"); setInputMode("p4_signal"); setRun(null); }} type="radio" /> Painel + lote de imagens</label></fieldset>
          <label className="mt-3 block text-sm font-medium" htmlFor="panel-capture">Primeira captura de referência do painel</label>
          <select className="mt-1 w-full rounded-md border p-2" id="panel-capture" onChange={(event) => { const id = event.target.value; setCaptureId(id); setEditingCaptureId(id); setAdditionalReferenceIds((current) => current.filter((item) => item !== id)); setTargetCaptureIds((current) => current.filter((item) => item !== id)); setTargetReferenceMap((current) => Object.fromEntries(Object.entries(current).map(([target, reference]) => [target, reference === captureId ? id : reference]))); setRun(null); }} value={captureId}>
            {!captures.length && <option value="">Nenhuma captura disponível</option>}
            {captures.map((capture) => <option key={capture.id} value={capture.id}>{capture.capture_key} — {capture.band_codes.join(", ")}</option>)}
          </select>
          {batchMode && <div className="mt-4 space-y-4">
            <p className="text-sm">{desktopLocal ? "Escolha uma ou mais capturas de referência. Desenhe a ROI da mesma cor separadamente em cada imagem; as posições podem ser diferentes. Com duas ou mais referências, seus coeficientes serão combinados e aplicados a todas as repetições." : "Selecione outras capturas do painel, se houver. Desenhe a ROI em cada referência e escolha explicitamente qual delas calibra cada captura de destino."}</p>
            <fieldset className="rounded-md border p-3 text-sm"><legend className="px-1 font-semibold">Outras capturas de referência</legend>
              {captures.filter((item) => item.id !== captureId).map((item) => <label className="flex items-center gap-2 py-1" key={item.id}><input checked={additionalReferenceIds.includes(item.id)} onChange={(event) => {
                const checked = event.target.checked;
                setAdditionalReferenceIds((current) => checked ? [...current, item.id] : current.filter((id) => id !== item.id));
                if (checked) setTargetCaptureIds((current) => current.filter((id) => id !== item.id));
                setTargetReferenceMap((current) => Object.fromEntries(Object.entries(current).filter(([id]) => !checked || id !== item.id).map(([id, reference]) => [id, !checked && reference === item.id ? captureId : reference])));
                setRun(null);
              }} type="checkbox" />{item.capture_key} · {item.band_count} bandas</label>)}
            </fieldset>
            <div className="flex items-center justify-between gap-2"><h4 className="text-sm font-semibold">Capturas a calibrar</h4><Button onClick={() => { const ids = captures.filter((item) => !referenceIds.includes(item.id) && item.band_count === 5).map((item) => item.id); setTargetCaptureIds(ids); setTargetReferenceMap((current) => Object.fromEntries(ids.map((id) => [id, current[id] && referenceIds.includes(current[id]) ? current[id] : captureId]))); setRun(null); }} type="button" variant="outline">Selecionar todas as completas</Button></div>
            <div className="max-h-64 space-y-2 overflow-y-auto rounded-md border p-3">{captures.filter((item) => !referenceIds.includes(item.id)).map((item) => <div className="flex flex-wrap items-center justify-between gap-2 text-sm" key={item.id}><label className="flex items-center gap-2"><input checked={targetCaptureIds.includes(item.id)} onChange={(event) => { const checked = event.target.checked; setTargetCaptureIds((current) => checked ? [...current, item.id] : current.filter((id) => id !== item.id)); setTargetReferenceMap((current) => { const next = { ...current }; if (checked) next[item.id] = captureId; else delete next[item.id]; return next; }); setRun(null); }} type="checkbox" />{item.replicate_label ? `${item.replicate_label} · ` : ""}{item.capture_key} · {item.band_count} bandas {item.band_count !== 5 ? "· incompleta" : ""}</label>{!desktopLocal && targetCaptureIds.includes(item.id) && <label>Referência <select className="ml-2 rounded-md border p-2" onChange={(event) => { setTargetReferenceMap((current) => ({ ...current, [item.id]: event.target.value })); setRun(null); }} value={targetReferenceMap[item.id] || captureId}>{referenceIds.map((id) => <option key={id} value={id}>{captures.find((capture) => capture.id === id)?.capture_key ?? id}</option>)}</select></label>}</div>)}</div>
            <div className="grid gap-3 sm:grid-cols-2"><label className="grid gap-1 text-sm">Nome do experimento/sessão<input className="rounded-md border p-2" onChange={(event) => setSessionName(event.target.value)} value={sessionName} /></label><label className="grid gap-1 text-sm">Data da captura<input className="rounded-md border p-2" onChange={(event) => setSessionDate(event.target.value)} type="date" value={sessionDate} /></label><label className="grid gap-1 text-sm">Fuso horário<input className="rounded-md border p-2" onChange={(event) => setSessionTimezone(event.target.value)} value={sessionTimezone} /></label><label className="grid gap-1 text-sm">Método de iluminação<select className="rounded-md border p-2" onChange={(event) => setIlluminationMethod(event.target.value as "stable_panel" | "not_assessed")} value={illuminationMethod}><option value="not_assessed">Estabilidade não verificada</option><option value="stable_panel">Iluminação considerada estável</option></select></label><label className="grid gap-1 text-sm sm:col-span-2">Condições de iluminação<input className="rounded-md border p-2" onChange={(event) => setIlluminationNotes(event.target.value)} placeholder="Ex.: céu limpo, painéis fotografados antes das plantas" value={illuminationNotes} /></label></div>
            <p className="text-xs text-amber-900">Confirme condições de iluminação comparáveis entre cada referência e as capturas associadas. O sistema não verifica irradiância automaticamente.</p>
          </div>}
          <p className="text-muted-foreground mt-2 text-xs">Confirme a sequência das bandas na página do dataset antes da calibração.</p>
        </div>
      </section>}

      {step === 1 && <section className="rounded-lg border bg-white p-5">
        <h2 className="text-xl font-semibold">2. Identificar bandas e sensores</h2>
        <p className="mt-2 text-sm">Referência exibida: <strong>{captures.find((item) => item.id === editingCaptureId)?.capture_key ?? "nenhuma"}</strong>.</p>
        <p className="text-muted-foreground my-2 text-sm">A associação automática deve ser conferida. A DJI usa cinco sensores fisicamente separados.</p>
        <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr className="border-b"><th className="p-2">Banda</th><th className="p-2">Arquivo</th><th className="p-2">Sensor</th><th className="p-2">λ (nm)</th><th className="p-2">Exposição (µs)</th><th className="p-2">Ganho</th><th className="p-2">Sinal DJI</th></tr></thead><tbody>{bands.map((band) => <tr className="border-b" key={band.id}><td className="p-2 font-semibold">{band.band}</td><td className="p-2">{band.filename}</td><td className="p-2">{metadataText(band.metadata.model)}</td><td className="p-2">{metadataText(band.metadata.wavelength_nm)}</td><td className="p-2">{metadataText(band.metadata.exposure_us)}</td><td className="p-2">{metadataText(band.metadata.sensor_gain)}</td><td className="p-2">{band.p4_signal_available ? "Disponível" : "Indisponível"}</td></tr>)}</tbody></table></div>
        <p className="mt-3 text-sm">Banda de referência para as ROIs propagadas: <select className="rounded-md border p-2" onChange={(event) => { setReferenceBand(event.target.value); setActiveBand(event.target.value); }} value={referenceBand}>{bands.map((band) => <option key={band.id} value={band.band}>{band.band}</option>)}</select></p>
        <p className="mt-3 text-xs text-amber-900">A propagação aplica os deslocamentos ópticos informados pela DJI. Revise visualmente cada banda e desenhe uma ROI individual se o alinhamento não corresponder ao painel.</p>
      </section>}

      {step === 2 && <section className="space-y-4 rounded-lg border bg-white p-5">
        <h2 className="text-xl font-semibold">3. Selecionar regiões do painel</h2>
        {batchMode && <label className="grid max-w-sm gap-2 text-sm font-medium">Captura de referência para desenhar as ROIs<select className="rounded-md border p-2" onChange={(event) => { setEditingCaptureId(event.target.value); setPolygonPoints([]); setPixel(null); setRun(null); }} value={editingCaptureId}>{referenceIds.map((id) => <option key={id} value={id}>{captures.find((item) => item.id === id)?.capture_key ?? id} · {(roisByCapture[id] ?? []).length} ROI(s)</option>)}</select></label>}
        {batchMode && <p className="text-sm text-amber-900">Cada referência precisa de ROI própria no alvo escolhido. Revise a posição nas cinco bandas, especialmente se o painel mudou de orientação.</p>}
        {batchMode && <label className="grid max-w-sm gap-2 text-sm font-medium">Alvo MAPIR usado para calcular K em todas as cinco bandas<select className="rounded-md border p-2" onChange={(event) => { setBatchFitSurface(event.target.value); setSurface(event.target.value); setWhiteAcknowledged(false); setRun(null); }} value={batchFitSurface}>{defaultSurfaces.map((name) => <option key={name}>{name}</option>)}</select></label>}
        {batchMode && batchFitSurface === "Branco" && !whiteAcknowledged && <div className="space-y-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm"><p>Atenção: nas avaliações anteriores com o Phantom 4 Multispectral e o painel MAPIR, houve diferenças entre a reflectância estimada do branco e a referência informada pelo fabricante. Confira saturação, iluminação, ROI e indicadores radiométricos antes de interpretar os resultados. O alvo branco permanece disponível para calibração.</p><div className="flex gap-2"><Button onClick={() => setWhiteAcknowledged(true)} type="button" variant="outline">Continuar com branco</Button><Button onClick={() => { setBatchFitSurface("Cinza-claro"); setSurface("Cinza-claro"); }} type="button" variant="outline">Escolher outro alvo</Button></div></div>}
        <div className="flex flex-wrap gap-3 text-sm">
          <label className="flex items-center gap-2"><input checked={pixelMode} onChange={(event) => setPixelMode(event.target.checked)} type="checkbox" /> Consultar DN de pixel</label>
          <label>Superfície <select className="ml-2 rounded-md border p-2" onChange={(event) => { setSurface(event.target.value); setWhiteAcknowledged(false); }} value={surface}>{defaultSurfaces.map((name) => <option key={name}>{name}</option>)}</select></label>
          <label>Banda <select className="ml-2 rounded-md border p-2" onChange={(event) => setActiveBand(event.target.value)} value={activeBand}>{bands.map((band) => <option key={band.id} value={band.band}>{band.band}</option>)}</select></label>
          <label>Ferramenta <select className="ml-2 rounded-md border p-2" onChange={(event) => { setGeometry(event.target.value as "rectangle" | "polygon"); setPolygonPoints([]); }} value={geometry}><option value="rectangle">Retângulo</option><option value="polygon">Polígono</option></select></label>
          {!batchMode && <label>Uso <select className="ml-2 rounded-md border p-2" onChange={(event) => setRole(event.target.value as "fit" | "validation")} value={role}><option value="fit">Ajuste</option><option value="validation">Validação independente</option></select></label>}
          <label className="flex items-center gap-2"><input checked={bandSpecific} onChange={(event) => setBandSpecific(event.target.checked)} type="checkbox" /> Ajuste individual desta banda</label>
        </div>
        {pixel && <p className="rounded-md border bg-emerald-50 p-3 text-sm">Pixel ({pixel.x}, {pixel.y}) · DN original: <strong>{formatted(pixel.dn, 1)}</strong> · sinal DJI corrigido: <strong>{formatted(pixel.camera_signal, 6)}</strong>. O brilho e o contraste da prévia não alteram estes valores.</p>}
        <div className="flex flex-wrap items-center gap-3 text-sm"><label>Zoom <input className="ml-2" max="2" min="0.5" onChange={(event) => setZoom(Number(event.target.value))} step="0.1" type="range" value={zoom} /></label><label>Brilho <input className="ml-2" max="2" min="0.5" onChange={(event) => setBrightness(Number(event.target.value))} step="0.1" type="range" value={brightness} /></label><label>Contraste <input className="ml-2" max="2" min="0.5" onChange={(event) => setContrast(Number(event.target.value))} step="0.1" type="range" value={contrast} /></label><span className="text-muted-foreground">Ajustes só na visualização.</span></div>
        <div className="max-h-[650px] overflow-auto rounded-lg bg-black p-2">
          {image?.preview_url ? <div className="relative mx-auto w-fit touch-none" onPointerDown={onPointerDown} onPointerUp={onPointerUp} style={{ width: `${String(Math.round(zoom * 100))}%`, maxWidth: "none" }}>
            <img alt={`Prévia da banda ${activeBand}`} className="block w-full select-none" draggable={false} ref={imageRef} src={image.preview_url} style={{ filter: `brightness(${String(brightness)}) contrast(${String(contrast)})` }} />
            <svg className="pointer-events-none absolute inset-0 h-full w-full" viewBox="0 0 1000 1000" preserveAspectRatio="none">
              {displayedRois.map((roi) => <polygon fill="rgba(0,180,120,.18)" key={`${roi.surface}-${roi.band ?? "base"}`} points={roi.coordinates.map(([x, y]) => svgPoint(x, y)).join(" ")} stroke={roi.role === "validation" ? "#f59e0b" : "#00d89b"} strokeWidth="3"><title>{roi.surface} · {roi.band ?? "propagada"}</title></polygon>)}
              {polygonPoints.length > 0 && <polyline fill="none" points={polygonPoints.map(([x, y]) => svgPoint(x, y)).join(" ")} stroke="#facc15" strokeWidth="3" />}
            </svg>
          </div> : <p className="p-10 text-center text-white">Selecione uma captura com prévias disponíveis.</p>}
        </div>
        <div className="flex flex-wrap gap-2">{geometry === "polygon" && <Button disabled={polygonPoints.length < 3} onClick={() => { saveROI(polygonPoints); setPolygonPoints([]); }} variant="outline">Concluir polígono</Button>}<Button onClick={() => setRois((current) => current.filter((roi) => !(roi.surface === surface && roi.band === (bandSpecific ? activeBand : null))))} variant="outline">Excluir ROI selecionada</Button></div>
        <ul className="grid gap-2 text-sm sm:grid-cols-2">{effectiveRois.map((roi) => <li className="rounded-md border p-2" key={`${roi.surface}-${roi.band ?? "base"}`}>{roi.surface} · {roi.band ?? `referência ${referenceBand} → demais bandas`} · {roi.role === "fit" ? "ajuste" : "checagem interna"}</li>)}</ul>
        <p className="text-xs text-amber-900">Selecione o interior de cada superfície, evitando bordas e inscrições. Para uma validação independente, reserve uma superfície que não participe da regressão.</p>
      </section>}

      {step === 3 && <section className="space-y-5 rounded-lg border bg-white p-5">
        <h2 className="text-xl font-semibold">4. Configurar a calibração</h2>
        <label className="grid max-w-sm gap-2 text-sm font-medium">Banda a calibrar<select className="rounded-md border p-2" disabled={batchMode} value={selectedBand} onChange={(event) => { setSelectedBand(event.target.value); if (event.target.value) setActiveBand(event.target.value); }}><option value="">Todas as bandas da captura</option>{primaryBands.map((band) => <option key={band.id} value={band.band}>Somente {band.band}</option>)}</select></label>
        {batchMode && <p className="text-sm text-emerald-900">Lote: {referenceIds.length} captura(s) de referência e {targetCaptureIds.length} captura(s) de destino, com cinco bandas por captura.</p>}
        {selectedBand && <p className="text-sm text-amber-900">A exportação conterá somente {selectedBand}. Para usar uma única superfície do painel, escolha o método Proporcional e desenhe a ROI dessa superfície em {selectedBand}.</p>}
        <div className="rounded-md border p-4"><label className="text-sm font-semibold">Painel salvo <select className="ml-2 rounded-md border p-2" onChange={(event) => { setPanelId(event.target.value); setRun(null); }} value={panelId}><option value="">Selecione um painel</option>{panels.map((panel) => <option key={panel.id} value={panel.id}>{panel.name} · {panel.source.certified ? "certificado declarado" : "não certificado"}</option>)}</select></label></div>
        <details className="rounded-md border p-4" open={!panels.length}><summary className="cursor-pointer font-semibold">Cadastrar painel e reflectâncias</summary>
          <div className="mt-4 grid gap-3 sm:grid-cols-2"><label className="grid gap-1 text-sm">Nome<input className="rounded-md border p-2" onChange={(event) => setPanelName(event.target.value)} value={panelName} /></label><label className="grid gap-1 text-sm">Fabricante<input className="rounded-md border p-2" onChange={(event) => setManufacturer(event.target.value)} value={manufacturer} /></label><label className="grid gap-1 text-sm">Modelo<input className="rounded-md border p-2" onChange={(event) => setModel(event.target.value)} value={model} /></label><label className="grid gap-1 text-sm">Número de série<input className="rounded-md border p-2" onChange={(event) => setSerial(event.target.value)} value={serial} /></label><label className="grid gap-1 text-sm sm:col-span-2">Origem dos valores<input className="rounded-md border p-2" onChange={(event) => setSourceLabel(event.target.value)} placeholder="Ex.: anotação manual do laboratório" value={sourceLabel} /></label></div>
          <p className="mt-4 text-sm text-muted-foreground">Preencha a reflectância da cor usada na ROI. As outras cores são opcionais.</p>
          <Button className="mt-2" onClick={() => { setValues(mapirProfile); setPanelName("MAPIR — perfil da especificação"); setSourceLabel("Perfil MAPIR informado na especificação técnica de 16/11/2026; sem certificado individual"); setCertified(false); setCsvText(""); }} type="button" variant="outline">Preencher perfil MAPIR da especificação</Button>
          {selectedBand && <Button className="mt-2" onClick={() => setShowAllSurfaces((current) => !current)} type="button" variant="outline">{showAllSurfaces ? "Mostrar apenas a cor da ROI" : "Mostrar outras cores (opcional)"}</Button>}
          <div className="mt-2 overflow-x-auto"><table className="w-full text-sm"><thead><tr><th className="p-2 text-left">Superfície</th>{(selectedBand ? [selectedBand] : spectralBands).map((band) => <th className="p-2" key={band}>{band}</th>)}</tr></thead><tbody>{panelSurfaces.map((name) => <tr key={name}><th className="p-2 text-left">{name}</th>{(selectedBand ? [selectedBand] : spectralBands).map((band) => <td className="p-1" key={band}><input aria-label={`${name} ${band}`} className="w-20 rounded border p-2" max="1" min="0" onChange={(event) => setValues((current) => ({ ...current, [name]: { ...current[name], [band]: event.target.value } }))} placeholder="0–1" step="any" type="number" value={values[name]?.[band] ?? ""} /></td>)}</tr>)}</tbody></table></div>
          <label className="mt-3 grid gap-1 text-sm">Importar certificado CSV (colunas surface,band,reflectance ou surface,BLUE,GREEN,RED,RED_EDGE,NIR)<input accept=".csv,text/csv" onChange={(event) => { const file = event.target.files?.[0]; if (file) void file.text().then(setCsvText); }} type="file" /></label>
          {csvText && <p className="text-sm text-emerald-800">CSV carregado. Os valores do CSV substituirão os campos manuais neste painel.</p>}
          <label className="mt-3 flex items-center gap-2 text-sm"><input checked={certified} disabled={!csvText} onChange={(event) => setCertified(event.target.checked)} type="checkbox" /> Declaro que o CSV corresponde ao certificado individual deste painel</label>
          <Button className="mt-4" disabled={busy || !panelName || !sourceLabel} onClick={() => void savePanel()}>Salvar painel</Button>
        </details>
        {selectedPanel && <p className="rounded-md bg-amber-50 p-3 text-sm">Valores selecionados: {selectedPanel.source.label}. {selectedPanel.source.certified ? "Certificado declarado pelo usuário; confira número de série e data." : "Sem certificado individual. O resultado será identificado como estimativa sem validação metrológica."}</p>}
        {whiteFit && !whiteAcknowledged && <div className="space-y-3 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm"><p>Atenção: nas avaliações anteriores com o Phantom 4 Multispectral e o painel MAPIR, houve diferenças entre a reflectância estimada do branco e a referência informada pelo fabricante. Confira saturação, iluminação, ROI e indicadores radiométricos antes de interpretar os resultados. O alvo branco permanece disponível para calibração.</p><div className="flex gap-2"><Button onClick={() => setWhiteAcknowledged(true)} type="button" variant="outline">Continuar com branco</Button><Button onClick={() => { if (batchMode) setBatchFitSurface("Cinza-claro"); else setRois((current) => current.filter((roi) => !(roi.surface === "Branco" && roi.role === "fit"))); setSurface("Cinza-claro"); setStep(2); }} type="button" variant="outline">Escolher outro alvo</Button></div></div>}
        {selectedPanel && calibrationIssues.length > 0 && <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm"><strong>Falta conferir a correspondência entre ROI e reflectância:</strong><ul className="mt-2 list-disc pl-5">{calibrationIssues.map((issue) => <li key={issue}>{issue}</li>)}</ul><Button className="mt-3" onClick={() => setStep(2)} type="button" variant="outline">Voltar às ROIs</Button></div>}
        <div className="grid gap-4 sm:grid-cols-2"><label className="grid gap-2 text-sm font-medium">Método<select className="rounded-md border p-2" disabled={batchMode} onChange={(event) => setMethod(event.target.value as "proportional" | "linear")} value={method}><option value="linear">Regressão linear por banda (≥2 superfícies)</option><option value="proportional">Proporcional (≥1 superfície)</option></select></label><label className="grid gap-2 text-sm font-medium">Medida de entrada<select className="rounded-md border p-2" onChange={(event) => setInputMode(event.target.value as "p4_signal" | "dn_empirical")} value={inputMode}><option disabled={!allP4} value="p4_signal">Sinal da câmera corrigido DJI {allP4 ? "" : "(metadados insuficientes)"}</option><option disabled={batchMode} value="dn_empirical">DN bruto — aproximação empírica</option></select></label></div>
        {inputMode === "dn_empirical" && <label className="flex gap-2 text-sm"><input checked={acknowledgeDN} onChange={(event) => setAcknowledgeDN(event.target.checked)} type="checkbox" /> Reconheço que DN não equivale a radiância e que variações de exposição e ganho podem invalidar a calibração.</label>}
        <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm">Calibração por irradiância solar indisponível: o fluxo exige coeficientes de conversão entre câmera e sensor solar que não foram identificados nestes metadados. Nenhum coeficiente será presumido.</div>
        <div className="flex gap-4 text-sm"><label><input checked={weighted} onChange={(event) => setWeighted(event.target.checked)} type="checkbox" /> Regressão ponderada pela variância</label><label><input checked={robust} onChange={(event) => setRobust(event.target.checked)} type="checkbox" /> Ajuste robusto Huber</label></div>
        <fieldset className="grid gap-3 rounded-md border p-4 sm:grid-cols-3"><legend className="px-2 text-sm font-semibold">Limites operacionais opcionais</legend><label className="grid gap-1 text-sm">CV máximo da ROI<input className="rounded-md border p-2" min="0" onChange={(event) => setMaxCv(event.target.value)} step="any" type="number" value={maxCv} /></label><label className="grid gap-1 text-sm">Saturação máxima (%)<input className="rounded-md border p-2" max="100" min="0" onChange={(event) => setMaxSaturation(event.target.value)} step="any" type="number" value={maxSaturation} /></label><label className="grid gap-1 text-sm">RMSE de validação máximo<input className="rounded-md border p-2" max="1" min="0" onChange={(event) => setMaxValidationRmse(event.target.value)} step="any" type="number" value={maxValidationRmse} /></label><p className="text-muted-foreground text-xs sm:col-span-3">Sem limites, a classificação operacional é “não avaliável”. O RMSE de validação exige ao menos uma superfície independente.</p></fieldset>
        <div className="flex gap-2"><Button disabled={busy || !panelId || !primaryRois.length} onClick={() => void startRun(false)} variant="outline">Salvar sessão</Button><Button disabled={busy || !panelId || !primaryRois.length || (inputMode === "dn_empirical" && !acknowledgeDN)} onClick={() => void startRun(true)}>Executar calibração</Button></div>
      </section>}

      {step === 4 && <section className="space-y-4 rounded-lg border bg-white p-5">
        <h2 className="text-xl font-semibold">5. Executar e auditar</h2>
        {!run && <p>Selecione uma sessão do histórico ou execute uma calibração na etapa anterior.</p>}
        {run && <>
          <p className="text-sm">Sessão {run.id} · estado: <strong>{run.state}</strong></p>
          <div className="flex gap-2">
            {run.state === "draft" && <Button onClick={() => void api.executeCalibrationRun(run.id).then(setRun).catch((cause: unknown) => setError(message(cause)))}>Executar sessão salva</Button>}
            {["queued", "running"].includes(run.state) && <Button onClick={() => void api.cancelCalibrationRun(run.id).then(setRun).catch((cause: unknown) => setError(message(cause)))} variant="outline">Cancelar lote</Button>}
            {Boolean(run.config.target_capture_ids?.length) && ["completed", "failed", "cancelled"].includes(run.state) && ((run.result?.target_summary?.failed ?? 0) > 0 || (run.result?.target_summary?.skipped ?? 0) > 0 || run.state === "failed") && <Button onClick={() => void api.retryFailedCalibrationCaptures(run.id).then((next) => { setRun(next); setRuns((current) => [next, ...current]); }).catch((cause: unknown) => setError(message(cause)))} variant="outline">Retomar capturas não concluídas</Button>}
          </div>
          {run.error && <p className="text-red-800" role="alert">{run.error}</p>}
        </>}
        {run?.result && <CalibrationResults activeBand={activeBand} onBandChange={setActiveBand} run={run} />}
      </section>}

      {step === 5 && <section className="space-y-4 rounded-lg border bg-white p-5"><h2 className="text-xl font-semibold">6. Exportar resultados</h2><p className="text-sm">{run?.result?.combined_models ? "O pacote contém as imagens TIFF Float32 com o nome do arquivo original em cada saída, além de combined-coefficients.csv, coeficientes individuais, auditoria JSON e relatório PDF. Cada repetição foi calibrada com a mesma combinação de referências." : "O pacote contém TIFF Float32 por banda e captura, CSV de estatísticas e coeficientes, JSON de auditoria e relatório PDF. Referências adicionais estão em references/<id>/; capturas de destino em captures/<id>/. O arquivo reference-coefficients.csv identifica o fator de cada referência."}</p><p className="text-sm text-amber-900">Para índices pixel a pixel, confira o alinhamento geométrico das cinco bandas de cada captura antes de calcular NDVI ou outros índices.</p><Button disabled={!run?.download_available} onClick={() => void download()}>Baixar pacote da calibração</Button>{!run?.download_available && <p className="text-muted-foreground text-sm">Execute uma sessão com referências válidas e aguarde a conclusão.</p>}{desktopLocal && <p className="text-sm"><a className="text-emerald-800 underline" href={`/datasets/${datasetId}#captures`}>Continuar para desenhar a ROI de análise</a></p>}</section>}

      <section className="rounded-lg border bg-white p-5"><h2 className="font-semibold">Histórico de calibrações</h2><div className="mt-3 flex flex-wrap gap-2">{runs.length ? runs.map((item) => <Button key={item.id} onClick={() => restore(item)} variant="outline">{new Date(item.created_at).toLocaleString("pt-BR")} · {item.state}</Button>) : <p className="text-muted-foreground text-sm">Nenhuma sessão salva.</p>}</div></section>
      <div className="flex justify-between"><Button disabled={step === 0} onClick={() => setStep((current) => current - 1)} variant="outline">Voltar</Button><Button disabled={step === steps.length - 1} onClick={() => setStep((current) => current + 1)}>Próxima etapa</Button></div>
    </div>
  );
}
