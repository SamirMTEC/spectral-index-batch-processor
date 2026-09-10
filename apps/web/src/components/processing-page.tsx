import {
  AlertTriangle,
  BarChart3,
  LoaderCircle,
  Play,
  RefreshCw,
  Save,
  Square,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  StatusBadge,
  TermHelp,
  WorkflowStepper,
} from "@/components/product-ui";
import {
  api,
  type AnalysisScope,
  type CaptureSummary,
  type ProcessingItem,
  type ProcessingJob,
  type ProcessingRecipe,
  type SpectralIndexDefinition,
  type SpectralIndexName,
} from "@/lib/api";

const terminal = new Set([
  "completed",
  "completed_with_errors",
  "failed",
  "cancelled",
]);

export function ProcessingPage({ datasetId }: { datasetId: string }) {
  const [jobs, setJobs] = useState<ProcessingJob[]>([]);
  const [recipes, setRecipes] = useState<ProcessingRecipe[]>([]);
  const [definitions, setDefinitions] = useState<SpectralIndexDefinition[]>([]);
  const [captures, setCaptures] = useState<CaptureSummary[]>([]);
  const [indexSearch, setIndexSearch] = useState("");
  const [recipeId, setRecipeId] = useState("");
  const [recipeName, setRecipeName] = useState("");
  const [activeId, setActiveId] = useState<string>();
  const [selected, setSelected] = useState<Set<SpectralIndexName>>(
    new Set(["NDVI"]),
  );
  const [analysisScopes, setAnalysisScopes] = useState<Set<AnalysisScope>>(
    new Set(["roi", "full_image"]),
  );
  const [saviL, setSaviL] = useState(0.5);
  const [arviGamma, setArviGamma] = useState(1);
  const [reflectanceScale, setReflectanceScale] = useState(1);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [savingRecipe, setSavingRecipe] = useState(false);
  const [error, setError] = useState<string>();
  const active = jobs.find((job) => job.id === activeId) ?? jobs[0];

  const load = useCallback(async () => {
    try {
      const [values, storedRecipes, spectralDefinitions, datasetCaptures] =
        await Promise.all([
          api.listProcessingJobs(datasetId),
          api.listProcessingRecipes(),
          api.listSpectralIndices(),
          api.listCaptures(datasetId),
        ]);
      setJobs(values);
      setRecipes(storedRecipes);
      setDefinitions(spectralDefinitions);
      setCaptures(datasetCaptures);
      setActiveId((current) => current ?? values[0]?.id);
      setError(undefined);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Falha ao carregar processamentos",
      );
    } finally {
      setLoading(false);
    }
  }, [datasetId]);

  useEffect(() => void load(), [load]);
  useEffect(() => {
    if (!active || terminal.has(active.state)) return;
    const interval = window.setInterval(() => {
      void api.getProcessingJob(active.id).then(
        (updated) =>
          setJobs((current) => [
            updated,
            ...current.filter((job) => job.id !== updated.id),
          ]),
        () => undefined,
      );
    }, 2_000);
    return () => window.clearInterval(interval);
  }, [active]);

  const runningIndices = useMemo(
    () =>
      Array.from(
        new Set(
          (active?.items ?? [])
            .filter((item) => item.state === "running")
            .map((item) => item.index_name),
        ),
      ),
    [active?.items],
  );
  const availableBands = useMemo(() => {
    if (!captures.length) return new Set<string>();
    const intersection = new Set(captures[0]?.band_codes ?? []);
    for (const capture of captures.slice(1)) {
      const current = new Set(capture.band_codes);
      for (const band of intersection) {
        if (!current.has(band)) intersection.delete(band);
      }
    }
    return intersection;
  }, [captures]);
  const filteredDefinitions = useMemo(() => {
    const query = indexSearch.trim().toLocaleLowerCase("pt-BR");
    if (!query) return definitions;
    return definitions.filter((definition) =>
      [
        definition.index,
        definition.description,
        definition.category,
        ...definition.aliases,
      ].some((value) => value.toLocaleLowerCase("pt-BR").includes(query)),
    );
  }, [definitions, indexSearch]);
  const incompatibleSelected = Array.from(selected).some((name) => {
    const definition = definitions.find((value) => value.index === name);
    return Boolean(
      definition && missingBands(definition, availableBands).length,
    );
  });

  async function start() {
    if (!selected.size || !analysisScopes.size || incompatibleSelected) return;
    setSubmitting(true);
    setError(undefined);
    try {
      const created = await api.createProcessingJob(
        datasetId,
        recipeId
          ? {
              recipe_id: recipeId,
              analysis_scopes: Array.from(analysisScopes),
            }
          : {
              indices: Array.from(selected).map((name) => ({
                name,
                parameters: indexParameters(
                  name,
                  saviL,
                  arviGamma,
                  reflectanceScale,
                ),
              })),
              analysis_scopes: Array.from(analysisScopes),
            },
        window.crypto.randomUUID(),
      );
      setJobs((current) => [
        created,
        ...current.filter((job) => job.id !== created.id),
      ]);
      setActiveId(created.id);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Não foi possível iniciar o lote",
      );
    } finally {
      setSubmitting(false);
    }
  }

  function applyRecipe(id: string) {
    setRecipeId(id);
    const recipe = recipes.find((value) => value.id === id);
    if (!recipe) return;
    setSelected(new Set(recipe.indices.map((value) => value.index)));
    const savi = recipe.indices.find((value) => value.index === "SAVI");
    if (savi?.parameters.L !== undefined) setSaviL(savi.parameters.L);
    const arvi = recipe.indices.find((value) => value.index === "ARVI");
    if (arvi?.parameters.gamma !== undefined)
      setArviGamma(arvi.parameters.gamma);
    const scale = recipe.indices[0]?.parameters.reflectance_scale;
    if (scale !== undefined) setReflectanceScale(scale);
  }

  async function saveRecipe() {
    if (!recipeName.trim() || !selected.size) return;
    setSavingRecipe(true);
    setError(undefined);
    try {
      const created = await api.createProcessingRecipe({
        name: recipeName.trim(),
        indices: Array.from(selected).map((name) => ({
          name,
          parameters: indexParameters(name, saviL, arviGamma, reflectanceScale),
        })),
        statistics: ["mean", "median", "std", "p05", "p25", "p75", "p95"],
      });
      setRecipes((current) => [...current, created]);
      setRecipeId(created.id);
      setRecipeName("");
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Não foi possível salvar a recipe",
      );
    } finally {
      setSavingRecipe(false);
    }
  }

  async function reprocess() {
    if (!active) return;
    setSubmitting(true);
    try {
      const created = await api.reprocessJob(active.id);
      setJobs((current) => [created, ...current]);
      setActiveId(created.id);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Não foi possível reprocessar o lote",
      );
    } finally {
      setSubmitting(false);
    }
  }

  async function cancel() {
    if (!active) return;
    const updated = await api.cancelProcessingJob(active.id);
    setJobs((current) => [
      updated,
      ...current.filter((job) => job.id !== updated.id),
    ]);
  }

  async function retry(item: ProcessingItem) {
    try {
      const updated = await api.retryProcessingItem(item.id);
      setJobs((current) => [
        updated,
        ...current.filter((job) => job.id !== updated.id),
      ]);
      setActiveId(updated.id);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Não foi possível repetir o item",
      );
    }
  }

  return (
    <div className="space-y-6">
      <WorkflowStepper
        current={active ? (terminal.has(active.state) ? 6 : 5) : 4}
        datasetId={datasetId}
        resultsHref={
          active && terminal.has(active.state)
            ? `/processing-jobs/${active.id}/results`
            : undefined
        }
      />
      <PageHeader
        description="Selecione os índices, os parâmetros e se deseja processar a ROI, a imagem completa ou ambas."
        eyebrow="Pipeline científico"
        title="Processamento"
      />

      <section className="surface p-6">
        <div>
          <h2 className="font-semibold">4. Configurar índices</h2>
          <p className="text-muted-foreground mt-1 text-sm">
            Use uma receita salva ou selecione manualmente. As fórmulas e
            parâmetros serão congelados no snapshot do job.
          </p>
        </div>
        <label className="mt-5 grid max-w-xl gap-2 text-sm font-medium">
          Receita de processamento
          <select
            className="border-input rounded-md border px-3 py-2"
            onChange={(event) => applyRecipe(event.target.value)}
            value={recipeId}
          >
            <option value="">Configuração manual</option>
            {recipes.map((recipe) => (
              <option key={recipe.id} value={recipe.id}>
                {recipe.name} ·{" "}
                {recipe.indices.map((value) => value.index).join(", ")}
              </option>
            ))}
          </select>
        </label>
        <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <label className="grid max-w-md flex-1 gap-2 text-sm font-medium">
            Pesquisar índice
            <input
              className="border-input rounded-md border px-3 py-2"
              onChange={(event) => setIndexSearch(event.target.value)}
              placeholder="Nome, sigla, categoria ou descrição"
              type="search"
              value={indexSearch}
            />
          </label>
          <div className="flex flex-wrap gap-2">
            <Button
              onClick={() => {
                setRecipeId("");
                setSelected(
                  new Set(
                    definitions
                      .filter(
                        (definition) =>
                          missingBands(definition, availableBands).length === 0,
                      )
                      .map((definition) => definition.index),
                  ),
                );
              }}
              type="button"
              variant="outline"
            >
              Selecionar compatíveis
            </Button>
            <Button
              onClick={() => {
                setRecipeId("");
                setSelected(new Set());
              }}
              type="button"
              variant="ghost"
            >
              Desmarcar todos
            </Button>
          </div>
        </div>
        <p className="text-muted-foreground mt-3 text-xs">
          Bandas presentes em todas as {captures.length} repetição(ões):{" "}
          {availableBands.size
            ? Array.from(availableBands).map(displayBand).join(", ")
            : "nenhuma banda científica comum"}
        </p>
        <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {filteredDefinitions.map((definition) => {
            const unavailable = missingBands(definition, availableBands);
            const name = definition.index;
            const incompatible = unavailable.length > 0;
            const disabled = incompatible && !selected.has(name);
            return (
              <label
                className={`flex items-start gap-3 rounded-lg border p-4 transition ${
                  disabled
                    ? "cursor-not-allowed opacity-55"
                    : selected.has(name)
                      ? "border-primary bg-primary/5 cursor-pointer"
                      : "border-border hover:bg-muted/50 cursor-pointer"
                }`}
                key={name}
              >
                <input
                  className="mt-1"
                  checked={selected.has(name)}
                  disabled={disabled}
                  onChange={() =>
                    setSelected((current) => {
                      setRecipeId("");
                      const next = new Set(current);
                      if (next.has(name)) next.delete(name);
                      else next.add(name);
                      return next;
                    })
                  }
                  type="checkbox"
                />
                <span className="min-w-0">
                  <span className="flex flex-wrap items-center gap-2">
                    <strong className="text-sm">{displayIndex(name)}</strong>
                    <small className="bg-muted rounded-full px-2 py-0.5 text-[0.65rem] font-medium">
                      {categoryLabel(definition.category)}
                    </small>
                    {definition.approximate && (
                      <small className="rounded-full bg-amber-100 px-2 py-0.5 text-[0.65rem] font-semibold text-amber-900">
                        Aproximado
                      </small>
                    )}
                  </span>
                  <small className="text-muted-foreground mt-1 block text-xs leading-4">
                    {definition.description}
                  </small>
                  <small className="mt-2 block text-xs font-medium">
                    {definition.required_bands.map(displayBand).join(" + ")}
                  </small>
                  <code className="text-muted-foreground mt-2 block text-[0.68rem] leading-4">
                    {definition.formula}
                  </code>
                  {incompatible && (
                    <small className="text-destructive mt-2 block text-xs font-medium">
                      Banda(s) ausente(s):{" "}
                      {unavailable.map(displayBand).join(", ")}
                    </small>
                  )}
                  {definition.scientific_note && (
                    <small className="mt-2 block text-xs leading-4 text-amber-800">
                      {definition.scientific_note}
                    </small>
                  )}
                </span>
              </label>
            );
          })}
        </div>
        {!filteredDefinitions.length && (
          <p className="text-muted-foreground mt-4 text-sm">
            Nenhum índice corresponde à pesquisa.
          </p>
        )}
        {selected.has("SAVI") && (
          <label className="mt-5 grid max-w-xs gap-2 text-sm font-medium">
            Fator L do <TermHelp term="SAVI" />
            <input
              className="border-input w-24 rounded-md border px-3 py-2"
              onChange={(event) => {
                setRecipeId("");
                setSaviL(Number(event.target.value));
              }}
              step="0.1"
              type="number"
              value={saviL}
            />
            <small className="text-muted-foreground font-normal">
              Valor recomendado inicial: 0,5. Ajuste apenas quando o protocolo
              exigir.
            </small>
          </label>
        )}
        {selected.has("ARVI") && (
          <label className="mt-5 grid max-w-xs gap-2 text-sm font-medium">
            Fator gamma do ARVI
            <input
              className="border-input w-24 rounded-md border px-3 py-2"
              onChange={(event) => {
                setRecipeId("");
                setArviGamma(Number(event.target.value));
              }}
              step="0.1"
              type="number"
              value={arviGamma}
            />
            <small className="text-muted-foreground font-normal">
              Valor padrão: 1.
            </small>
          </label>
        )}
        <label className="mt-5 grid max-w-md gap-2 text-sm font-medium">
          Escala de reflectância
          <input
            className="border-input w-32 rounded-md border px-3 py-2"
            min="0.0000001"
            onChange={(event) => {
              setRecipeId("");
              setReflectanceScale(Number(event.target.value));
            }}
            step="any"
            type="number"
            value={reflectanceScale}
          />
          <small className="text-muted-foreground leading-5 font-normal">
            Use 1 para reflectância 0–1. Para dados escalados, informe o divisor
            documentado pelo sensor (por exemplo, 10.000). O sistema nunca
            presume a escala automaticamente.
          </small>
        </label>
        {incompatibleSelected && (
          <p className="text-destructive mt-4 text-sm font-medium">
            A receita selecionada contém índices incompatíveis com as bandas
            comuns do dataset. Remova esses índices ou revise a associação de
            bandas.
          </p>
        )}
        <div className="mt-6">
          <h3 className="text-sm font-semibold">Área de análise</h3>
          <p className="text-muted-foreground mt-1 text-xs leading-5">
            Cada opção produz estatísticas e GeoTIFFs independentes. Selecione
            ambas para comparar o recorte com a cena completa; os downloads
            permanecem separados nos resultados.
          </p>
          <div className="mt-3 grid max-w-2xl gap-3 sm:grid-cols-2">
            {(
              [
                {
                  value: "roi",
                  title: "Recorte da ROI",
                  description:
                    "Calcula somente os pixels dentro da ROI confirmada.",
                },
                {
                  value: "full_image",
                  title: "Imagem completa",
                  description:
                    "Calcula toda a grade original, respeitando NoData e pixels inválidos.",
                },
              ] as const
            ).map((option) => (
              <label
                className={`flex cursor-pointer items-start gap-3 rounded-lg border p-4 transition ${analysisScopes.has(option.value) ? "border-primary bg-primary/5" : "border-border hover:bg-muted/50"}`}
                key={option.value}
              >
                <input
                  checked={analysisScopes.has(option.value)}
                  className="mt-1"
                  onChange={() =>
                    setAnalysisScopes((current) => {
                      const next = new Set(current);
                      if (next.has(option.value)) next.delete(option.value);
                      else next.add(option.value);
                      return next;
                    })
                  }
                  type="checkbox"
                />
                <span>
                  <strong className="text-sm">{option.title}</strong>
                  <small className="text-muted-foreground mt-1 block text-xs leading-4">
                    {option.description}
                  </small>
                </span>
              </label>
            ))}
          </div>
          {!analysisScopes.size && (
            <p className="text-destructive mt-2 text-xs">
              Selecione ao menos uma área de análise.
            </p>
          )}
        </div>
        <div className="bg-muted/50 mt-6 max-w-2xl rounded-lg p-4">
          <p className="text-sm font-semibold">Salvar para reutilizar</p>
          <p className="text-muted-foreground mt-1 text-xs">
            A receita guarda índices, versões de fórmula, parâmetros e
            estatísticas.
          </p>
          <div className="mt-3 flex gap-2">
            <input
              className="border-input min-w-0 flex-1 rounded-md border px-3 py-2 text-sm"
              onChange={(event) => setRecipeName(event.target.value)}
              placeholder="Nome para salvar esta recipe"
              value={recipeName}
            />
            <Button
              disabled={
                savingRecipe ||
                !recipeName.trim() ||
                !selected.size ||
                incompatibleSelected ||
                !Number.isFinite(reflectanceScale) ||
                reflectanceScale <= 0
              }
              onClick={() => void saveRecipe()}
              type="button"
              variant="outline"
            >
              <Save /> Salvar recipe
            </Button>
          </div>
        </div>
        <Button
          className="mt-6"
          disabled={
            submitting ||
            selected.size === 0 ||
            analysisScopes.size === 0 ||
            incompatibleSelected ||
            !Number.isFinite(reflectanceScale) ||
            reflectanceScale <= 0
          }
          onClick={() => void start()}
        >
          {submitting ? <LoaderCircle className="animate-spin" /> : <Play />}
          5. Iniciar processamento
        </Button>
      </section>

      {error && <ErrorState message={error} />}
      {loading && (
        <LoadingState label="Carregando histórico de processamentos" />
      )}
      {!loading && jobs.length === 0 && (
        <EmptyState
          description="Configure ao menos um índice acima. O progresso real aparecerá assim que o worker receber o lote."
          icon="results"
          title="Nenhum processamento iniciado"
        />
      )}

      {jobs.length > 0 && (
        <select
          className="border-input w-full rounded-md border px-3 py-2 text-sm"
          onChange={(event) => setActiveId(event.target.value)}
          value={active?.id}
        >
          {jobs.map((job) => (
            <option key={job.id} value={job.id}>
              {new Date(job.created_at).toLocaleString("pt-BR")} ·{" "}
              {job.indices.join(", ")} · {job.state}
            </option>
          ))}
        </select>
      )}

      {active && (
        <section className="surface p-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <h2 className="text-lg font-semibold">
                Lote {active.id.slice(0, 8)}
              </h2>
              <div className="mt-2">
                <StatusBadge value={active.state} />
              </div>
            </div>
            {!terminal.has(active.state) && (
              <Button onClick={() => void cancel()} variant="outline">
                <Square /> Cancelar
              </Button>
            )}
            {terminal.has(active.state) && (
              <div className="flex flex-wrap gap-2">
                <Button
                  disabled={submitting || !active.snapshot_checksum}
                  onClick={() => void reprocess()}
                  variant="outline"
                >
                  <RefreshCw /> Reprocessar com os mesmos parâmetros
                </Button>
                <Button asChild variant="outline">
                  <a href={`/processing-jobs/${active.id}/reproducibility`}>
                    Reprodutibilidade
                  </a>
                </Button>
                <Button asChild variant="outline">
                  <a href={`/processing-jobs/${active.id}/results`}>
                    <BarChart3 /> Ver resultados
                  </a>
                </Button>
              </div>
            )}
          </div>
          <div
            aria-label={`Progresso: ${String(Math.round(active.progress * 100))}%`}
            className="bg-muted mt-5 h-3 overflow-hidden rounded-full"
            role="progressbar"
            aria-valuemax={100}
            aria-valuemin={0}
            aria-valuenow={Math.round(active.progress * 100)}
          >
            <div
              className="bg-primary h-full transition-[width]"
              style={{
                width: `${String(Math.min(100, active.progress * 100))}%`,
              }}
            />
          </div>
          <p className="mt-2 text-sm font-medium">
            {active.processed_items} / {active.total_items} itens (
            {Math.round(active.progress * 100)}%)
          </p>
          <div className="mt-5 grid gap-3 text-sm sm:grid-cols-4">
            <Metric label="Concluídos" value={active.completed_items} />
            <Metric label="Falhas" value={active.failed_items} />
            <Metric label="Ignorados" value={active.skipped_items} />
            <Metric
              label="Tempo decorrido"
              value={formatDuration(active.elapsed_seconds)}
            />
          </div>
          <p className="text-muted-foreground mt-4 text-sm">
            Índices em cálculo:{" "}
            {runningIndices.length ? runningIndices.join(", ") : "nenhum"}
          </p>
          {active.warnings.length > 0 && (
            <div className="mt-4 rounded-md border border-amber-500/50 bg-amber-50 p-3 text-sm text-amber-950">
              <p className="flex items-center gap-2 font-medium">
                <AlertTriangle className="size-4" /> Avisos
              </p>
              {active.warnings.map((warning) => (
                <p className="mt-1" key={warning}>
                  {warning}
                </p>
              ))}
            </div>
          )}
          <div className="mt-6 overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-sm">
              <thead className="text-muted-foreground border-b">
                <tr>
                  <th className="py-2">Repetição</th>
                  <th>Índice</th>
                  <th>Área analisada</th>
                  <th>Estado</th>
                  <th>Tentativas</th>
                  <th>Resultado</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {active.items.map((item) => (
                  <tr
                    className="border-border border-b align-top"
                    key={item.id}
                  >
                    <td className="py-3">{item.capture ?? "—"}</td>
                    <td>
                      {item.index_name}
                      <small className="text-muted-foreground block">
                        fórmula {item.formula_version}
                      </small>
                    </td>
                    <td>{scopeLabel(item.analysis_scope)}</td>
                    <td>
                      <StatusBadge value={item.state} />
                      {item.message && (
                        <small className="text-destructive block max-w-xs">
                          {item.message}
                        </small>
                      )}
                    </td>
                    <td>
                      {item.attempts}/{item.max_attempts}
                    </td>
                    <td>
                      {item.result_available ? "GeoTIFF + estatísticas" : "—"}
                    </td>
                    <td>
                      {(item.state === "failed" ||
                        item.state === "skipped") && (
                        <Button
                          onClick={() => void retry(item)}
                          size="sm"
                          variant="ghost"
                        >
                          <RefreshCw /> Repetir
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="bg-muted rounded-md p-3">
      <span className="text-muted-foreground block">{label}</span>
      <strong className="text-lg">{value}</strong>
    </div>
  );
}

function displayIndex(name: SpectralIndexName) {
  if (name === "CIGREEN") return "CIgreen";
  if (name === "CIRED_EDGE") return "CIred-edge";
  if (name === "MNDVI") return "mNDVI";
  if (name === "MARI") return "mARI";
  if (name === "NIRV") return "NIRv";
  if (name === "EXG") return "ExG";
  if (name === "EXR") return "ExR";
  if (name === "EXGR") return "ExGR";
  return name;
}

function displayBand(name: string) {
  return name === "RED_EDGE" ? "RedEdge" : name;
}

function missingBands(
  definition: SpectralIndexDefinition,
  availableBands: Set<string>,
) {
  return definition.required_bands.filter((band) => !availableBands.has(band));
}

function categoryLabel(category: string) {
  const labels: Record<string, string> = {
    atmosphere_resistant: "Atmosfera",
    chlorophyll: "Clorofila",
    pigments: "Pigmentos",
    red_edge: "RedEdge",
    rgb_visible: "RGB / Visível",
    soil_adjusted: "Ajustado ao solo",
    vegetation: "Vegetação",
  };
  return labels[category] ?? category;
}

function indexParameters(
  name: SpectralIndexName,
  saviL: number,
  arviGamma: number,
  reflectanceScale: number,
) {
  const parameters: Record<string, number> = {
    reflectance_scale: reflectanceScale,
  };
  if (name === "SAVI") parameters.L = saviL;
  if (name === "ARVI") parameters.gamma = arviGamma;
  return parameters;
}

function scopeLabel(value: "roi" | "full_image") {
  return value === "roi" ? "Recorte da ROI" : "Imagem completa";
}

function formatDuration(seconds: number) {
  const rounded = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(rounded / 60);
  return `${String(minutes)}m ${String(rounded % 60)}s`;
}
