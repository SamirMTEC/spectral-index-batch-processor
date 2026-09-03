import { AlertTriangle, Download, LoaderCircle } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  EmptyState as ProductEmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  StatusBadge,
  WorkflowStepper,
} from "@/components/product-ui";
import {
  api,
  type AnalysisScope,
  type ExportFormat,
  type ProcessingResults,
  type ResultImage,
  type ResultTableRow,
} from "@/lib/api";

type Tab =
  "overview" | "table" | "images" | "errors" | "files" | "reproducibility";
type Sort = "treatment" | "repetition" | "index" | "mean" | "valid_pixels";

const tabs: { id: Tab; label: string }[] = [
  { id: "overview", label: "Visão geral" },
  { id: "table", label: "Tabela" },
  { id: "images", label: "Imagens" },
  { id: "errors", label: "Erros" },
  { id: "files", label: "Arquivos" },
  { id: "reproducibility", label: "Reprodutibilidade" },
];

export function ResultsPage({
  jobId,
  initialTab = "overview",
}: {
  jobId: string;
  initialTab?: Tab;
}) {
  const [tab, setTab] = useState<Tab>(initialTab);
  const [data, setData] = useState<ProcessingResults>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [search, setSearch] = useState("");
  const [treatment, setTreatment] = useState("");
  const [index, setIndex] = useState("");
  const [scope, setScope] = useState<AnalysisScope | "">("");
  const [sort, setSort] = useState<Sort>("repetition");
  const [descending, setDescending] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(
        await api.getProcessingResults(jobId, {
          ...(search ? { q: search } : {}),
          ...(treatment ? { treatment } : {}),
          ...(index ? { index } : {}),
          ...(scope ? { scope } : {}),
          sort,
          descending,
        }),
      );
      setError(undefined);
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Falha ao carregar resultados",
      );
    } finally {
      setLoading(false);
    }
  }, [descending, index, jobId, scope, search, sort, treatment]);

  useEffect(() => void load(), [load]);
  useEffect(() => {
    if (
      !data?.files.some(
        (file) => file.state === "queued" || file.state === "generating",
      )
    ) {
      return;
    }
    const timer = window.setTimeout(() => void load(), 2_000);
    return () => window.clearTimeout(timer);
  }, [data?.files, load]);

  async function createExports(formats: ExportFormat[]) {
    try {
      await api.createExports(jobId, formats);
      await load();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Falha ao gerar exportação",
      );
    }
  }

  async function reprocess() {
    try {
      const created = await api.reprocessJob(jobId);
      window.location.href = `/datasets/${created.dataset_id}/processing`;
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Não foi possível reprocessar o job",
      );
    }
  }

  return (
    <div className="space-y-6">
      <WorkflowStepper
        current={6}
        datasetId={data?.overview.dataset_id}
        resultsHref={`/processing-jobs/${jobId}/results`}
      />
      <PageHeader
        description={
          <>
            Job <span className="font-mono text-xs">{jobId.slice(0, 8)}</span> ·
            resultados individuais, artefatos científicos e contexto de
            reprodução.
          </>
        }
        eyebrow="Análise concluída"
        title="Resultados"
      />

      <nav
        aria-label="Abas de resultados"
        className="surface flex gap-1 overflow-x-auto p-1.5"
      >
        {tabs.map((item) => (
          <button
            className={`rounded-md px-3 py-2 text-sm font-medium whitespace-nowrap ${
              tab === item.id
                ? "bg-primary text-primary-foreground shadow-sm"
                : "text-muted-foreground hover:bg-muted hover:text-foreground"
            }`}
            key={item.id}
            onClick={() => setTab(item.id)}
            type="button"
          >
            {item.label}
          </button>
        ))}
      </nav>

      {error && <ErrorState message={error} retry={() => void load()} />}
      {loading && !data && <LoadingState label="Carregando resultados" />}
      {data && (
        <>
          {tab === "overview" && <Overview data={data} />}
          {tab === "table" && (
            <Table
              data={data}
              descending={descending}
              index={index}
              search={search}
              scope={scope}
              setDescending={setDescending}
              setIndex={setIndex}
              setSearch={setSearch}
              setScope={setScope}
              setSort={setSort}
              setTreatment={setTreatment}
              sort={sort}
              treatment={treatment}
            />
          )}
          {tab === "images" && <Images images={data.images} />}
          {tab === "errors" && <Errors data={data} />}
          {tab === "files" && <Files data={data} onCreate={createExports} />}
          {tab === "reproducibility" && (
            <Reproducibility data={data} onReprocess={reprocess} />
          )}
        </>
      )}
    </div>
  );
}

function Overview({ data }: { data: ProcessingResults }) {
  const overview = data.overview;
  const metrics = [
    ["Dataset", overview.dataset_name],
    ["Tratamentos", overview.treatment_count],
    ["Repetições", overview.repetition_count],
    ["Imagens", overview.image_count],
    ["Índices", overview.indices.join(", ") || "—"],
    ["Pixels analisados", formatInteger(overview.analyzed_pixels)],
    ["Pixels na ROI", formatInteger(overview.analyzed_pixels_roi)],
    [
      "Pixels na imagem completa",
      formatInteger(overview.analyzed_pixels_full_image),
    ],
    ["Erros", overview.error_count],
    ["Data da análise", formatDate(overview.analyzed_at)],
  ];
  return (
    <div className="space-y-6">
      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {metrics.map(([label, value]) => (
          <div className="metric-card min-h-28" key={label}>
            <p className="metric-card__label">{label}</p>
            <p className="relative z-10 mt-2 text-lg font-semibold">{value}</p>
          </div>
        ))}
      </section>
      <section className="border-border rounded-xl border p-5">
        <h2 className="text-lg font-semibold">Resumo por tratamento</h2>
        <p className="text-muted-foreground mt-1 text-sm">
          Média e desvio padrão das médias por repetição. Não há inferência
          estatística automática.
        </p>
        {data.treatment_summary.length === 0 ? (
          <Empty label="Sem resultados individuais para resumir." />
        ) : (
          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[500px] text-left text-sm">
              <thead className="text-muted-foreground border-b">
                <tr>
                  <th className="py-2">Tratamento</th>
                  <th>Índice</th>
                  <th>Área analisada</th>
                  <th>n</th>
                  <th>Média</th>
                  <th>Desvio padrão</th>
                </tr>
              </thead>
              <tbody>
                {data.treatment_summary.map((summary) => (
                  <tr
                    className="border-b"
                    key={`${summary.treatment ?? "no-treatment"}-${summary.index_name}-${summary.analysis_scope}`}
                  >
                    <td className="py-2">
                      {summary.treatment ?? "Não informado"}
                    </td>
                    <td>{summary.index_name}</td>
                    <td>{scopeLabel(summary.analysis_scope)}</td>
                    <td>{summary.n}</td>
                    <td>{formatNumber(summary.mean)}</td>
                    <td>{formatNumber(summary.std)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function Table(props: {
  data: ProcessingResults;
  search: string;
  treatment: string;
  index: string;
  scope: AnalysisScope | "";
  sort: Sort;
  descending: boolean;
  setSearch: (value: string) => void;
  setTreatment: (value: string) => void;
  setIndex: (value: string) => void;
  setScope: (value: AnalysisScope | "") => void;
  setSort: (value: Sort) => void;
  setDescending: (value: boolean) => void;
}) {
  return (
    <section className="space-y-4">
      <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-6">
        <input
          className="border-input rounded-md border px-3 py-2 text-sm"
          onChange={(event) => props.setSearch(event.target.value)}
          placeholder="Pesquisar repetição ou índice"
          value={props.search}
        />
        <select
          className="border-input rounded-md border px-3 py-2 text-sm"
          onChange={(event) => props.setTreatment(event.target.value)}
          value={props.treatment}
        >
          <option value="">Todos os tratamentos</option>
          {props.data.table.treatments.map((value) => (
            <option key={value}>{value}</option>
          ))}
        </select>
        <select
          className="border-input rounded-md border px-3 py-2 text-sm"
          onChange={(event) =>
            props.setScope(event.target.value as AnalysisScope | "")
          }
          value={props.scope}
        >
          <option value="">Todas as áreas</option>
          <option value="roi">Somente recorte da ROI</option>
          <option value="full_image">Somente imagem completa</option>
        </select>
        <select
          className="border-input rounded-md border px-3 py-2 text-sm"
          onChange={(event) => props.setIndex(event.target.value)}
          value={props.index}
        >
          <option value="">Todos os índices</option>
          {props.data.table.indices.map((value) => (
            <option key={value}>{value}</option>
          ))}
        </select>
        <select
          className="border-input rounded-md border px-3 py-2 text-sm"
          onChange={(event) => props.setSort(event.target.value as Sort)}
          value={props.sort}
        >
          <option value="repetition">Ordenar por repetição</option>
          <option value="treatment">Ordenar por tratamento</option>
          <option value="index">Ordenar por índice</option>
          <option value="mean">Ordenar por média</option>
          <option value="valid_pixels">Ordenar por pixels válidos</option>
        </select>
        <Button
          onClick={() => props.setDescending(!props.descending)}
          variant="outline"
        >
          {props.descending ? "Decrescente" : "Crescente"}
        </Button>
      </div>
      <p className="text-muted-foreground text-sm">
        {props.data.table.total} linha(s) encontrada(s).
      </p>
      <div className="border-border overflow-x-auto rounded-xl border">
        <table className="w-full min-w-[1500px] text-left text-sm">
          <thead className="bg-muted text-muted-foreground">
            <tr>
              {[
                "Tratamento",
                "Repetição",
                "Índice",
                "Área analisada",
                "Mean",
                "Median",
                "Min",
                "Max",
                "Std",
                "P05",
                "P25",
                "P75",
                "P95",
                "Valid Pixels",
              ].map((name) => (
                <th className="px-3 py-3 font-medium" key={name}>
                  {name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {props.data.table.rows.map((row) => (
              <TableRow key={row.result_id} row={row} />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function TableRow({ row }: { row: ResultTableRow }) {
  const values = [
    row.mean,
    row.median,
    row.min,
    row.max,
    row.std,
    row.p05,
    row.p25,
    row.p75,
    row.p95,
  ];
  return (
    <tr className="border-border border-t">
      <td className="px-3 py-3">{row.treatment ?? "Não informado"}</td>
      <td className="px-3">{row.repetition ?? row.capture ?? "—"}</td>
      <td className="px-3 font-medium">{row.index_name}</td>
      <td className="px-3">{scopeLabel(row.analysis_scope)}</td>
      {values.map((value, position) => (
        <td className="px-3 tabular-nums" key={position}>
          {formatNumber(value)}
        </td>
      ))}
      <td className="px-3 tabular-nums">{formatInteger(row.valid_pixels)}</td>
    </tr>
  );
}

function Images({ images }: { images: ResultImage[] }) {
  const [selected, setSelected] = useState<ResultImage>();
  const [preview, setPreview] = useState<string>();
  const [error, setError] = useState<string>();
  const current = selected ?? images[0];
  useEffect(() => {
    if (!current) return;
    let active = true;
    let url: string | undefined;
    void api.getIndexResultPreview(current.result_id).then(
      (value) => {
        url = value;
        if (active) {
          setPreview(value);
          setError(undefined);
        } else URL.revokeObjectURL(value);
      },
      (cause: unknown) =>
        active &&
        setError(
          cause instanceof Error ? cause.message : "Falha ao carregar preview",
        ),
    );
    return () => {
      active = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [current]);
  if (!images.length || !current)
    return <Empty label="Não há imagens de índice concluídas neste job." />;
  return (
    <section className="space-y-4">
      <p className="text-muted-foreground text-sm">
        O resultado à direita é um preview pseudocolor, produzido apenas para
        visualização. O GeoTIFF float32 permanece inalterado.
      </p>
      <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
        <div className="border-border max-h-[600px] space-y-2 overflow-y-auto rounded-xl border p-3">
          {images.map((image) => (
            <button
              className={`w-full rounded-md border p-3 text-left text-sm ${current.result_id === image.result_id ? "border-primary bg-primary/5" : "border-border"}`}
              key={image.result_id}
              onClick={() => {
                setPreview(undefined);
                setSelected(image);
              }}
              type="button"
            >
              <strong>{image.index_name}</strong>
              <span className="text-primary mt-1 block text-xs font-medium">
                {scopeLabel(image.analysis_scope)}
              </span>
              <span className="text-muted-foreground mt-1 block">
                {image.treatment ?? "Sem tratamento"} ·{" "}
                {image.repetition ?? image.capture ?? "—"}
              </span>
            </button>
          ))}
        </div>
        <div className="grid gap-4 lg:grid-cols-2">
          <ImagePanel
            label={
              current.original_label
                ? `Original · ${current.original_label}`
                : "Imagem original"
            }
            src={current.original_preview_url}
          />
          <ImagePanel
            {...(error ? { error } : {})}
            label={`${current.index_name} · preview pseudocolor`}
            loading={!preview && !error}
            {...(preview ? { src: preview } : {})}
          />
        </div>
      </div>
    </section>
  );
}

function ImagePanel({
  label,
  src,
  loading,
  error,
}: {
  label: string;
  src?: string | null;
  loading?: boolean;
  error?: string;
}) {
  return (
    <figure className="border-border overflow-hidden rounded-xl border">
      <figcaption className="border-b px-4 py-3 text-sm font-medium">
        {label}
      </figcaption>
      <div className="bg-muted flex min-h-[340px] items-center justify-center">
        {loading ? (
          <LoaderCircle className="text-muted-foreground animate-spin" />
        ) : src ? (
          <img
            alt={label}
            className="max-h-[640px] w-full object-contain"
            src={src}
          />
        ) : (
          <p className="text-muted-foreground p-6 text-center text-sm">
            {error ?? "Preview indisponível para esta imagem."}
          </p>
        )}
      </div>
    </figure>
  );
}

function Errors({ data }: { data: ProcessingResults }) {
  if (!data.errors.length)
    return <Empty label="Este job não possui erros ou itens ignorados." />;
  return (
    <section className="space-y-3">
      {data.errors.map((error) => (
        <article
          className="border-border rounded-xl border p-4"
          key={error.item_id}
        >
          <p className="flex items-center gap-2 font-semibold">
            <AlertTriangle className="size-4 text-amber-600" /> {error.problem}
          </p>
          <dl className="text-muted-foreground mt-3 grid gap-2 text-sm sm:grid-cols-3">
            <div>
              <dt>Arquivo</dt>
              <dd className="text-foreground">
                {error.file ?? "Não identificado"}
              </dd>
            </div>
            <div>
              <dt>Repetição</dt>
              <dd className="text-foreground">
                {error.repetition ?? "Não identificada"}
              </dd>
            </div>
            <div>
              <dt>Área analisada</dt>
              <dd className="text-foreground">
                {scopeLabel(error.analysis_scope)}
              </dd>
            </div>
            <div>
              <dt>Ação recomendada</dt>
              <dd className="text-foreground">{error.recommended_action}</dd>
            </div>
          </dl>
        </article>
      ))}
    </section>
  );
}

function Files({
  data,
  onCreate,
}: {
  data: ProcessingResults;
  onCreate: (formats: ExportFormat[]) => Promise<void>;
}) {
  return (
    <section className="space-y-3">
      <div className="surface p-5">
        <h2 className="font-semibold">Exportação científica</h2>
        <p className="text-muted-foreground mt-1 text-sm">
          CSV, XLSX, ROIs, manifesto e logs são gerados a partir do snapshot
          deste job.
        </p>
        <div className="mt-5 grid gap-4 lg:grid-cols-2">
          <div className="border-border rounded-lg border p-4">
            <h3 className="text-sm font-semibold">Tabela completa</h3>
            <p className="text-muted-foreground mt-1 text-xs leading-5">
              Uma linha por repetição e índice, com todas as estatísticas e a
              quantidade de pixels válidos.
            </p>
            <div className="mt-3 flex gap-2">
              <Button
                onClick={() => void onCreate(["csv"])}
                size="sm"
                variant="outline"
              >
                <Download /> CSV completo
              </Button>
              <Button
                onClick={() => void onCreate(["xlsx"])}
                size="sm"
                variant="outline"
              >
                <Download /> XLSX completo
              </Button>
            </div>
          </div>
          <div className="border-border rounded-lg border p-4">
            <h3 className="text-sm font-semibold">Médias gerais</h3>
            <p className="text-muted-foreground mt-1 text-xs leading-5">
              Média e desvio-padrão entre as repetições, agrupados por
              tratamento e índice.
            </p>
            <div className="mt-3 flex gap-2">
              <Button
                onClick={() => void onCreate(["summary_csv"])}
                size="sm"
                variant="outline"
              >
                <Download /> CSV das médias
              </Button>
              <Button
                onClick={() => void onCreate(["summary_xlsx"])}
                size="sm"
                variant="outline"
              >
                <Download /> XLSX das médias
              </Button>
            </div>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap gap-2">
            <Button
              onClick={() => void onCreate(["rois"])}
              size="sm"
              variant="outline"
            >
              ROIs
            </Button>
            <Button
              onClick={() => void onCreate(["manifest"])}
              size="sm"
              variant="outline"
            >
              Manifesto
            </Button>
            <Button
              onClick={() => void onCreate(["logs"])}
              size="sm"
              variant="outline"
            >
              Logs
            </Button>
          </div>
          <Button onClick={() => void onCreate(["complete"])}>
            <Download /> Baixar pacote completo
          </Button>
        </div>
        <div className="border-border mt-4 rounded-lg border p-4">
          <h3 className="text-sm font-semibold">GeoTIFFs dos índices</h3>
          <p className="text-muted-foreground mt-1 text-xs leading-5">
            Escolha se deseja baixar somente os índices do recorte da ROI ou
            somente os índices calculados sobre a imagem completa.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button
              onClick={() => void onCreate(["rasters_roi"])}
              size="sm"
              variant="outline"
            >
              <Download /> Baixar índices da ROI
            </Button>
            <Button
              onClick={() => void onCreate(["rasters_full_image"])}
              size="sm"
              variant="outline"
            >
              <Download /> Baixar índices da imagem completa
            </Button>
          </div>
        </div>
        <p className="text-muted-foreground mt-4 text-xs leading-5">
          O pacote completo reúne tabelas, GeoTIFFs, ROIs, configuração,
          manifesto e logs sem modificar os rasters científicos originais.
        </p>
      </div>
      {!data.files.length && (
        <ProductEmptyState
          description="Escolha um formato acima. A geração ocorre em segundo plano e esta lista será atualizada automaticamente."
          icon="results"
          title="Nenhum arquivo gerado"
        />
      )}
      {data.files.map((file) => (
        <article
          className="surface flex flex-wrap items-center justify-between gap-4 p-4"
          key={file.id}
        >
          <div>
            <p className="font-medium">{file.filename ?? file.artifact_type}</p>
            <p className="text-muted-foreground mt-1 text-sm">
              {file.artifact_type} ·{" "}
              {file.size_bytes === null
                ? "tamanho indisponível"
                : formatBytes(file.size_bytes)}{" "}
              · SHA-256: {file.checksum_sha256 ?? "indisponível"}
            </p>
            {file.state !== "ready" && (
              <div className="mt-2">
                <StatusBadge value={file.state} />
              </div>
            )}
          </div>
          {file.download_url && (
            <Button asChild>
              <a href={file.download_url}>
                <Download /> Baixar
              </a>
            </Button>
          )}
        </article>
      ))}
    </section>
  );
}

function Reproducibility({
  data,
  onReprocess,
}: {
  data: ProcessingResults;
  onReprocess: () => Promise<void>;
}) {
  const manifest = data.reproducibility;
  return (
    <section className="space-y-6">
      <div className="border-border rounded-xl border p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold">
              Como esta análise foi produzida
            </h2>
            <p className="text-muted-foreground mt-1 text-sm">
              Snapshot {manifest.snapshot_schema_version} · SHA-256{" "}
              {manifest.snapshot_checksum || "legado sem checksum"}
            </p>
          </div>
          <Button
            disabled={!manifest.snapshot_checksum}
            onClick={() => void onReprocess()}
            variant="outline"
          >
            Reprocessar com os mesmos parâmetros
          </Button>
        </div>
        <dl className="mt-5 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-3">
          <div>
            <dt className="text-muted-foreground">Sistema</dt>
            <dd>SpectralIndex {manifest.software_version}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Radiometria</dt>
            <dd>{radiometryLabel(manifest.radiometry_mode)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Executado em</dt>
            <dd>{formatDate(manifest.created_at)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Concluído em</dt>
            <dd>{formatDate(manifest.completed_at)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Recipe</dt>
            <dd>{manifest.recipe_name ?? "Configuração manual"}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Job de origem</dt>
            <dd>{manifest.parent_job_id ?? "Execução original"}</dd>
          </div>
        </dl>
      </div>

      <div>
        <h2 className="text-lg font-semibold">
          Índices, fórmulas e parâmetros
        </h2>
        <div className="mt-3 grid gap-3 lg:grid-cols-2">
          {manifest.formulas.map((formula) => (
            <article
              className="border-border rounded-xl border p-4"
              key={`${formula.index}-${formula.formula_version}`}
            >
              <p className="font-semibold">
                {formula.index} · fórmula {formula.formula_version}
              </p>
              <code className="bg-muted mt-2 block overflow-x-auto rounded p-3 text-xs">
                {formula.formula}
              </code>
              <p className="text-muted-foreground mt-2 text-sm">
                Bandas: {formula.required_bands.join(" + ")}
              </p>
              <p className="text-muted-foreground mt-1 text-sm">
                Parâmetros: {humanParameters(formula.parameters)}
              </p>
            </article>
          ))}
        </div>
      </div>

      <div>
        <h2 className="text-lg font-semibold">
          Arquivos, bandas e ROI congelados
        </h2>
        <div className="mt-3 space-y-3">
          {manifest.captures.map((capture) => (
            <details
              className="border-border rounded-xl border p-4"
              key={capture.capture_id}
            >
              <summary className="cursor-pointer font-medium">
                {capture.repetition ?? capture.capture} ·{" "}
                {capture.treatment ?? "sem tratamento"}
              </summary>
              <div className="mt-3 text-sm">
                {capture.roi ? (
                  <p>
                    ROI “{capture.roi.name}” ({capture.roi.geometry_type}), área{" "}
                    {formatInteger(Math.round(capture.roi.area_pixels))} px,
                    grade {capture.roi.reference_width} ×{" "}
                    {capture.roi.reference_height}, checksum{" "}
                    {capture.roi.geometry_checksum}
                  </p>
                ) : (
                  <p className="text-destructive">
                    Nenhuma ROI fazia parte deste snapshot.
                  </p>
                )}
                <ul className="mt-3 list-disc space-y-1 pl-5">
                  {capture.band_mappings.map((band) => (
                    <li key={`${band.band}-${band.asset_id}`}>
                      <strong>{band.band}</strong>: {band.filename} · SHA-256{" "}
                      {band.checksum_sha256 ?? "indisponível"}
                    </li>
                  ))}
                </ul>
              </div>
            </details>
          ))}
        </div>
      </div>

      <div>
        <h2 className="text-lg font-semibold">Resultados por item</h2>
        <div className="mt-3 space-y-3">
          {manifest.inputs.map((input) => (
            <details
              className="border-border rounded-xl border p-4"
              key={input.item_id}
            >
              <summary className="cursor-pointer font-medium">
                {input.index_name} · fórmula {input.formula_version} ·{" "}
                {input.repetition ?? input.capture ?? input.item_id}
              </summary>
              <div className="text-muted-foreground mt-3 space-y-2 text-sm">
                <p>Fórmula: {input.formula}</p>
                <p>Área analisada: {scopeLabel(input.analysis_scope)}</p>
                <p>Versão da ROI: {input.roi_version_id ?? "não disponível"}</p>
                <p>Parâmetros: {humanParameters(input.parameters)}</p>
                <p>
                  Resultado: versão{" "}
                  {input.result_object_version_id ?? "não disponível"} ·
                  checksum {input.result_checksum_sha256 ?? "não disponível"}
                </p>
                <ul className="list-disc pl-5">
                  {input.source_bands.map((band) => (
                    <li
                      key={`${band.band_code ?? "band"}-${band.filename ?? "file"}`}
                    >
                      {band.band_code ?? "Banda"}: {band.filename ?? "arquivo"}{" "}
                      · {band.sha256 ?? "checksum indisponível"}
                    </li>
                  ))}
                </ul>
              </div>
            </details>
          ))}
        </div>
      </div>
    </section>
  );
}

function humanParameters(parameters: Record<string, unknown>) {
  const entries = Object.entries(parameters);
  return entries.length
    ? entries.map(([name, value]) => `${name} = ${String(value)}`).join(", ")
    : "nenhum";
}

function radiometryLabel(value: string) {
  return (
    (
      {
        unknown: "Desconhecida",
        digital_number: "Digital number",
        reflectance: "Reflectância",
      } as Record<string, string>
    )[value] ?? value
  );
}

function scopeLabel(value: AnalysisScope) {
  return value === "roi" ? "Recorte da ROI" : "Imagem completa";
}

function Empty({ label }: { label: string }) {
  return (
    <div className="border-border text-muted-foreground rounded-xl border border-dashed p-8 text-center text-sm">
      {label}
    </div>
  );
}
function formatNumber(value: number | null) {
  return value === null ? "—" : value.toFixed(4);
}
function formatInteger(value: number) {
  return new Intl.NumberFormat("pt-BR").format(value);
}
function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString("pt-BR") : "—";
}
function formatBytes(value: number) {
  return value < 1024 * 1024
    ? `${(value / 1024).toFixed(1)} KB`
    : `${(value / (1024 * 1024)).toFixed(1)} MB`;
}
