import {
  Archive,
  ArrowRight,
  Database,
  FolderPlus,
  Leaf,
  LogOut,
  Plus,
  ScanLine,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { type FormEvent, useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  SectionHeading,
  StatusBadge,
  TermHelp,
  WorkflowStepper,
} from "@/components/product-ui";
import { ROIEditor } from "@/components/roi-editor";
import { ProcessingPage } from "@/components/processing-page";
import { ResultsPage } from "@/components/results-page";
import { UploadPanel } from "@/components/upload-panel";
import {
  api,
  Dashboard,
  Dataset,
  DatasetInput,
  BandSequenceProfile,
  Project,
  ProjectInput,
} from "@/lib/api";
import { useApplicationAuth } from "@/lib/auth-context";

type LoadState<T> =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "success"; data: T };

function go(path: string) {
  window.history.pushState({}, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}
function usePath() {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const update = () => setPath(window.location.pathname);
    window.addEventListener("popstate", update);
    return () => window.removeEventListener("popstate", update);
  }, []);
  return path;
}
function useData<T>(loader: () => Promise<T>, key: string) {
  const [state, setState] = useState<LoadState<T>>({ status: "loading" });
  const refresh = useCallback(() => {
    setState({ status: "loading" });
    void loader().then(
      (data) => setState({ status: "success", data }),
      (error: unknown) =>
        setState({
          status: "error",
          message: error instanceof Error ? error.message : "Erro inesperado",
        }),
    );
  }, [loader]);
  useEffect(refresh, [key, refresh]);
  return [state, refresh] as const;
}
function Brand() {
  return (
    <span className="brand">
      <span className="brand__mark">
        <Leaf aria-hidden="true" />
      </span>
      <span className="brand__text">SpectralIndex</span>
    </span>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  const path = window.location.pathname;
  const auth = useApplicationAuth();
  const identity = auth.user?.name ?? auth.user?.email ?? "Pesquisador";
  return (
    <div className="app-shell bg-background text-foreground">
      <header className="app-header">
        <div className="app-header__inner">
          <a aria-label="SpectralIndex — dashboard" href="/dashboard">
            <Brand />
          </a>
          <nav aria-label="Navegação principal" className="app-nav">
            <a
              aria-current={path === "/dashboard" ? "page" : undefined}
              href="/dashboard"
            >
              Dashboard
            </a>
            <a
              aria-current={path.startsWith("/projects") ? "page" : undefined}
              href="/projects"
            >
              Projetos
            </a>
            <span className="app-identity" title={auth.user?.email}>
              <span className="app-identity__dot" /> {identity}
            </span>
            {auth.enabled && (
              <Button
                aria-label="Sair"
                onClick={() => void auth.signOut()}
                size="sm"
                variant="ghost"
              >
                <LogOut /> Sair
              </Button>
            )}
          </nav>
        </div>
      </header>
      <main className="app-main">{children}</main>
    </div>
  );
}
function Heading({
  title,
  action,
}: {
  title: string;
  action?: React.ReactNode;
}) {
  return (
    <PageHeader action={action} eyebrow="Área do pesquisador" title={title} />
  );
}
function State({
  state,
  empty,
  retry,
}: {
  state: LoadState<unknown>;
  empty?: boolean;
  retry: () => void;
}) {
  if (state.status === "loading") return <LoadingState />;
  if (state.status === "error")
    return <ErrorState message={state.message} retry={retry} />;
  if (empty)
    return (
      <EmptyState
        description="Crie o primeiro projeto para organizar datasets, ROIs e análises em um só lugar."
        title="Ainda não há dados para exibir."
      />
    );
  return null;
}
function ProjectList({ projects }: { projects: Project[] }) {
  return (
    <div className="mt-4 grid gap-3 md:grid-cols-2">
      {projects.map((p) => (
        <button
          className="surface group hover:border-primary/30 flex min-h-32 items-start justify-between gap-5 p-5 text-left transition hover:-translate-y-0.5 hover:shadow-md"
          key={p.id}
          onClick={() => go(`/projects/${p.id}`)}
          type="button"
        >
          <span className="min-w-0">
            <strong className="text-base">{p.name}</strong>
            <small className="text-muted-foreground mt-2 line-clamp-2 block leading-5">
              {p.description || "Sem descrição"}
            </small>
          </span>
          <StatusBadge value={p.status} />
        </button>
      ))}
    </div>
  );
}
function DatasetList({ datasets }: { datasets: Dataset[] }) {
  return (
    <div className="mt-4 grid gap-3">
      {datasets.map((d) => (
        <button
          className="surface group hover:border-primary/30 hover:bg-muted/50 flex items-center justify-between gap-4 p-4 text-left transition"
          key={d.id}
          onClick={() => go(`/datasets/${d.id}`)}
          type="button"
        >
          <span>
            <strong>{d.name}</strong>
            <small className="text-muted-foreground mt-1 block">
              {d.sensor || "Sensor não informado"} ·{" "}
              {radiometryLabel(d.radiometry_mode)}
            </small>
          </span>
          <StatusBadge value={d.state} />
        </button>
      ))}
    </div>
  );
}

function LandingPage() {
  return (
    <div className="landing">
      <header className="landing-nav">
        <a href="/">
          <Brand />
        </a>
        <Button asChild variant="outline">
          <a href="/login">Acessar plataforma</a>
        </Button>
      </header>
      <main>
        <section className="landing-main">
          <div className="landing-copy">
            <p className="eyebrow">Processamento multiespectral rastreável</p>
            <h1>Da imagem bruta ao resultado científico.</h1>
            <p>
              Organize bandas, delimite a região de interesse e processe índices
              espectrais em lote — com artefatos, estatísticas e parâmetros
              preservados.
            </p>
            <div className="landing-actions">
              <Button asChild size="lg">
                <a href="/login">
                  Iniciar uma análise <ArrowRight />
                </a>
              </Button>
              <Button asChild size="lg" variant="outline">
                <a href="#workflow">Conhecer o fluxo</a>
              </Button>
            </div>
          </div>
          <div
            aria-label="Representação das cinco bandas científicas"
            className="science-window"
          >
            <div className="science-window__bar">
              <i />
              <i />
              <i />
            </div>
            <div className="science-window__content">
              <span className="science-window__label">
                Captura multiespectral · 5 bandas
              </span>
              <div className="spectral-strip">
                <span />
                <span />
                <span />
                <span />
                <span />
              </div>
              <div className="science-window__stats">
                <div>
                  <small>Bandas</small>
                  <strong>Blue → NIR</strong>
                </div>
                <div>
                  <small>Saída</small>
                  <strong>GeoTIFF float32</strong>
                </div>
                <div>
                  <small>Rastreabilidade</small>
                  <strong>Checksum + manifesto</strong>
                </div>
              </div>
            </div>
          </div>
        </section>
        <section className="landing-section" id="workflow">
          <div className="landing-section__head">
            <p className="eyebrow">Ciência sem caixa-preta</p>
            <h2>Um fluxo guiado, com decisões explícitas.</h2>
            <p>
              Cada etapa informa o que está pronto, o que precisa de revisão e o
              que será usado no cálculo.
            </p>
          </div>
          <div className="landing-grid">
            <article className="landing-card">
              <Database />
              <h3>Dados organizados</h3>
              <p>
                Uploads grandes, revisão de bandas e originais preservados de
                forma imutável.
              </p>
            </article>
            <article className="landing-card">
              <ScanLine />
              <h3>Seleção precisa</h3>
              <p>
                ROI em coordenadas da imagem original, com conferência entre
                bandas.
              </p>
            </article>
            <article className="landing-card">
              <ShieldCheck />
              <h3>Resultado reproduzível</h3>
              <p>
                Fórmulas versionadas, parâmetros, checksums, logs e manifesto
                científico.
              </p>
            </article>
          </div>
        </section>
      </main>
      <footer className="landing-footer">
        SpectralIndex Batch Processor · análise espectral transparente e
        reproduzível.
      </footer>
    </div>
  );
}

function LoginPage({ returnTo = "/dashboard" }: { returnTo?: string }) {
  const auth = useApplicationAuth();

  useEffect(() => {
    if (auth.enabled && auth.isAuthenticated) go(returnTo);
  }, [auth.enabled, auth.isAuthenticated, returnTo]);

  return (
    <main className="login-layout">
      <aside className="login-aside">
        <a href="/">
          <Brand />
        </a>
        <div>
          <h1>Seu workspace científico, pronto para analisar.</h1>
          <p>
            Projetos, datasets e resultados permanecem separados por pesquisador
            e documentados para reprodução futura.
          </p>
        </div>
      </aside>
      <section className="login-panel">
        <div className="login-card">
          <p className="eyebrow">Acesso seguro</p>
          <h2>Entrar no SpectralIndex</h2>
          <p>
            Continue para abrir o ambiente de pesquisa disponível nesta
            instalação.
          </p>
          <Button
            className="mt-5 w-full"
            disabled={auth.isLoading || Boolean(auth.error)}
            onClick={() =>
              auth.enabled ? void auth.signIn(returnTo) : go(returnTo)
            }
            size="lg"
          >
            {auth.isLoading ? "Verificando acesso…" : "Entrar com segurança"}{" "}
            <ArrowRight />
          </Button>
          {auth.error && (
            <div className="login-note" role="alert">
              <strong>Configuração necessária.</strong> {auth.error}
            </div>
          )}
          <div className="login-note">
            <strong>
              {auth.enabled ? "Identidade protegida." : "Ambiente local."}
            </strong>{" "}
            {auth.enabled
              ? "O acesso é autenticado pelo Auth0; sua senha não é armazenada pelo SpectralIndex."
              : "A identidade de desenvolvimento é resolvida automaticamente."}
          </div>
        </div>
      </section>
    </main>
  );
}

function radiometryLabel(value: Dataset["radiometry_mode"]) {
  return value === "reflectance"
    ? "Reflectância"
    : value === "digital_number"
      ? "DN"
      : "Radiometria não informada";
}

function DashboardPage() {
  const [state, retry] = useData<Dashboard>(api.getDashboard, "dashboard");
  return (
    <Shell>
      <PageHeader
        description="Acompanhe seus projetos e retome análises recentes."
        eyebrow="Workspace científico"
        title="Dashboard"
        action={
          <Button onClick={() => go("/projects/new")}>
            <Plus />
            Novo projeto
          </Button>
        }
      />
      {state.status === "loading" && (
        <LoadingState label="Carregando dashboard" />
      )}
      {state.status === "error" && (
        <ErrorState message={state.message} retry={retry} />
      )}
      {state.status === "success" && state.data.project_count === 0 && (
        <EmptyState
          action={
            <Button onClick={() => go("/projects/new")}>
              <Plus /> Criar primeiro projeto
            </Button>
          }
          description="Projetos mantêm datasets, configurações e histórico de processamento organizados por estudo."
          title="Ainda não há dados para exibir."
        />
      )}
      {state.status === "success" && state.data.project_count > 0 && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <section className="metric-card">
              <p className="metric-card__label">Projetos</p>
              <p className="metric-card__value">{state.data.project_count}</p>
            </section>
            <section className="metric-card">
              <p className="metric-card__label">Datasets</p>
              <p className="metric-card__value">{state.data.dataset_count}</p>
            </section>
            <section className="metric-card sm:col-span-2 lg:col-span-1">
              <p className="metric-card__label">Fluxo recomendado</p>
              <p className="mt-3 text-sm font-semibold">
                Upload → Bandas → ROI → Índices
              </p>
              <p className="text-muted-foreground mt-2 text-xs leading-5">
                Revise cada decisão antes de processar.
              </p>
            </section>
          </div>
          <div className="mt-10">
            <SectionHeading
              description="Seus estudos atualizados mais recentemente."
              title="Projetos recentes"
            />
          </div>
          <ProjectList projects={state.data.recent_projects} />
        </>
      )}
    </Shell>
  );
}
function ProjectsPage() {
  const [state, retry] = useData<Project[]>(api.listProjects, "projects");
  return (
    <Shell>
      <PageHeader
        description="Agrupe datasets do mesmo estudo e mantenha o histórico de análise em contexto."
        eyebrow="Biblioteca de estudos"
        title="Projetos"
        action={
          <Button onClick={() => go("/projects/new")}>
            <Plus />
            Novo projeto
          </Button>
        }
      />
      <State
        empty={state.status === "success" && !state.data.length}
        retry={retry}
        state={state}
      />
      {state.status === "success" && state.data.length > 0 && (
        <ProjectList projects={state.data} />
      )}
    </Shell>
  );
}
function ProjectForm({
  initial,
  submit,
}: {
  initial?: Project;
  submit: (input: ProjectInput) => Promise<void>;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [error, setError] = useState<string>();
  const [saving, setSaving] = useState(false);
  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(undefined);
    try {
      await submit({ name, description: description || undefined });
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Não foi possível salvar",
      );
    } finally {
      setSaving(false);
    }
  }
  return (
    <form
      className="surface max-w-2xl space-y-5 p-6"
      onSubmit={(event) => void save(event)}
    >
      <label className="grid gap-2 text-sm font-medium">
        Nome do projeto
        <input
          className="border-input rounded-md border px-3 py-2"
          maxLength={160}
          onChange={(e) => setName(e.target.value)}
          required
          value={name}
        />
        <small className="text-muted-foreground font-normal">
          Use um nome que identifique cultura, safra ou experimento.
        </small>
      </label>
      <label className="grid gap-2 text-sm font-medium">
        Descrição
        <textarea
          className="border-input min-h-28 rounded-md border px-3 py-2"
          onChange={(e) => setDescription(e.target.value)}
          value={description}
        />
      </label>
      {error && <ErrorState message={error} />}
      <div className="flex flex-wrap gap-2">
        <Button disabled={saving}>
          {saving ? "Salvando…" : "Salvar projeto"}
        </Button>
        <Button onClick={() => go("/projects")} type="button" variant="ghost">
          Cancelar
        </Button>
      </div>
    </form>
  );
}
function NewProject() {
  return (
    <Shell>
      <PageHeader
        description="O projeto será o contêiner para seus datasets e análises."
        eyebrow="Novo estudo"
        title="Novo projeto"
      />
      <ProjectForm
        submit={async (input) => {
          const project = await api.createProject(input);
          go(`/projects/${project.id}`);
        }}
      />
    </Shell>
  );
}
function ProjectPage({ id }: { id: string }) {
  const loader = useCallback(
    () => Promise.all([api.getProject(id), api.listDatasets(id)]),
    [id],
  );
  const [state, retry] = useData(loader, id);
  async function archive() {
    if (window.confirm("Arquivar este projeto?")) {
      await api.archiveProject(id);
      retry();
    }
  }
  return (
    <Shell>
      <State retry={retry} state={state} />
      {state.status === "success" && (
        <>
          <Heading
            title={state.data[0].name}
            action={
              <div className="flex gap-2">
                <Button
                  disabled={state.data[0].status === "archived"}
                  onClick={() => go(`/projects/${id}/datasets/new`)}
                >
                  <FolderPlus />
                  Novo dataset
                </Button>
                <Button
                  disabled={state.data[0].status === "archived"}
                  onClick={() => void archive()}
                  variant="outline"
                >
                  <Archive />
                  Arquivar
                </Button>
              </div>
            }
          />
          <div className="surface p-5">
            <div className="flex items-center justify-between gap-4">
              <StatusBadge value={state.data[0].status} />
              <span className="text-muted-foreground text-xs">
                Atualizado em{" "}
                {new Date(state.data[0].updated_at).toLocaleDateString("pt-BR")}
              </span>
            </div>
            <p className="text-muted-foreground mt-4 leading-6">
              {state.data[0].description || "Sem descrição informada."}
            </p>
          </div>
          <div className="mt-10">
            <SectionHeading
              description="Cada dataset representa uma coleta ou conjunto coerente de imagens."
              title="Datasets"
            />
          </div>
          {state.data[1].length ? (
            <DatasetList datasets={state.data[1]} />
          ) : (
            <div className="mt-4">
              <EmptyState
                action={
                  <Button
                    disabled={state.data[0].status === "archived"}
                    onClick={() => go(`/projects/${id}/datasets/new`)}
                  >
                    <FolderPlus /> Criar dataset
                  </Button>
                }
                description="Adicione uma coleta para iniciar o upload e a revisão das bandas."
                title="Nenhum dataset neste projeto"
              />
            </div>
          )}
        </>
      )}
    </Shell>
  );
}
function DatasetForm({ projectId }: { projectId: string }) {
  const [form, setForm] = useState<DatasetInput>({
    name: "",
    radiometry_mode: "unknown",
  });
  const [error, setError] = useState<string>();
  const [saving, setSaving] = useState(false);
  const set = (field: keyof DatasetInput, value: string) =>
    setForm((current) => ({ ...current, [field]: value }));
  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    try {
      const dataset = await api.createDataset(projectId, form);
      go(`/datasets/${dataset.id}`);
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Não foi possível salvar",
      );
    } finally {
      setSaving(false);
    }
  }
  const fields: [keyof DatasetInput, string][] = [
    ["description", "Descrição"],
    ["crop", "Cultura"],
    ["experiment", "Experimento"],
    ["location", "Localização"],
    ["sensor", "Sensor"],
    ["notes", "Notas"],
  ];
  return (
    <form
      className="surface grid max-w-3xl gap-5 p-6 sm:grid-cols-2"
      onSubmit={(e) => void save(e)}
    >
      <label className="grid gap-2 text-sm font-medium sm:col-span-2">
        Nome do dataset
        <input
          className="border-input rounded-md border px-3 py-2"
          onChange={(e) => set("name", e.target.value)}
          required
          value={form.name}
        />
      </label>
      {fields.map(([field, label]) => (
        <label className="grid gap-2 text-sm font-medium" key={field}>
          {label}
          <input
            className="border-input rounded-md border px-3 py-2"
            onChange={(e) => set(field, e.target.value)}
            value={form[field] ?? ""}
          />
        </label>
      ))}
      <label className="grid gap-2 text-sm font-medium">
        <span>
          Modo radiométrico <TermHelp term="reflectance" />
        </span>
        <select
          className="border-input rounded-md border px-3 py-2"
          onChange={(e) => set("radiometry_mode", e.target.value)}
          value={form.radiometry_mode}
        >
          <option value="unknown">Desconhecido</option>
          <option value="digital_number">DN — valor digital bruto</option>
          <option value="reflectance">Reflectância</option>
        </select>
        <small className="text-muted-foreground font-normal">
          <TermHelp label="Entenda DN" term="DN" />
        </small>
      </label>
      {error && (
        <div className="sm:col-span-2">
          <ErrorState message={error} />
        </div>
      )}
      <div className="flex gap-2 sm:col-span-2">
        <Button disabled={saving}>
          {saving ? "Salvando…" : "Criar dataset"}
        </Button>
        <Button
          onClick={() => go(`/projects/${projectId}`)}
          type="button"
          variant="ghost"
        >
          Cancelar
        </Button>
      </div>
    </form>
  );
}
function NewDataset({ id }: { id: string }) {
  return (
    <Shell>
      <PageHeader
        description="Descreva a coleta antes de enviar as imagens. Essas informações acompanharão os resultados."
        eyebrow="Preparar coleta"
        title="Novo dataset"
      />
      <DatasetForm projectId={id} />
    </Shell>
  );
}
function DatasetPage({ id }: { id: string }) {
  const loader = useCallback(() => api.getDataset(id), [id]);
  const [state, retry] = useData(loader, id);
  async function remove() {
    if (
      state.status === "success" &&
      window.confirm("Excluir este dataset em rascunho?")
    ) {
      await api.deleteDataset(id);
      go(`/projects/${state.data.project_id}`);
    }
  }
  return (
    <Shell>
      <State retry={retry} state={state} />
      {state.status === "success" && (
        <>
          <PageHeader
            description={
              state.data.description ||
              "Configure a ingestão, revise as bandas e delimite as regiões que serão analisadas."
            }
            eyebrow="Dataset"
            title={state.data.name}
            action={
              <div className="flex gap-2">
                <Button onClick={() => go(`/datasets/${id}/processing`)}>
                  <ScanLine /> Processar índices
                </Button>
                <Button onClick={() => void remove()} variant="destructive">
                  <Trash2 /> Excluir rascunho
                </Button>
              </div>
            }
          />
          <WorkflowStepper
            current={
              state.data.state === "ready"
                ? 4
                : state.data.state === "needs_review"
                  ? 2
                  : 1
            }
            datasetId={id}
          />
          <div className="surface grid gap-5 p-5 sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <span className="text-muted-foreground block text-xs font-medium tracking-wide uppercase">
                Estado
              </span>
              <span className="mt-2 block">
                <StatusBadge value={state.data.state} />
              </span>
            </div>
            <div>
              <span className="text-muted-foreground block text-xs font-medium tracking-wide uppercase">
                Radiometria
              </span>
              <strong className="mt-2 block text-sm">
                {radiometryLabel(state.data.radiometry_mode)}
              </strong>
            </div>
            <div>
              <span className="text-muted-foreground block text-xs font-medium tracking-wide uppercase">
                Sensor
              </span>
              <strong className="mt-2 block text-sm">
                {state.data.sensor || "Não informado"}
              </strong>
            </div>
            <div>
              <span className="text-muted-foreground block text-xs font-medium tracking-wide uppercase">
                Localização
              </span>
              <strong className="mt-2 block text-sm">
                {state.data.location || "Não informada"}
              </strong>
            </div>
          </div>
          <div id="upload">
            <UploadPanel datasetId={state.data.id} />
          </div>
          <div id="bands">
            <BandSequenceSettings dataset={state.data} onSaved={retry} />
          </div>
          <div id="captures">
            <CaptureList datasetId={state.data.id} />
          </div>
        </>
      )}
    </Shell>
  );
}

function BandSequenceSettings({
  dataset,
  onSaved,
}: {
  dataset: Dataset;
  onSaved: () => void;
}) {
  const [profiles, setProfiles] = useState<BandSequenceProfile[]>([]);
  const [profileId, setProfileId] = useState(
    dataset.band_sequence_profile_id ?? "",
  );
  const [error, setError] = useState<string>();
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [name, setName] = useState("");
  const [prefix, setPrefix] = useState("DJI_");
  const [capturePrefix, setCapturePrefix] = useState("DJI_");
  const [order, setOrder] = useState("BLUE, GREEN, RED, RED_EDGE, NIR");

  useEffect(() => {
    void api.listBandSequenceProfiles().then(
      (items) => {
        setProfiles(items);
        setProfileId(
          (currentProfileId) => currentProfileId || items[0]?.id || "",
        );
      },
      (cause: unknown) =>
        setError(
          cause instanceof Error
            ? cause.message
            : "Falha ao carregar sequências",
        ),
    );
  }, []);

  async function selectProfile() {
    if (!profileId) return;
    setSaving(true);
    setSaved(false);
    setError(undefined);
    try {
      await api.assignBandSequenceProfile(dataset.id, profileId);
      onSaved();
      setSaved(true);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Não foi possível salvar a sequência",
      );
    } finally {
      setSaving(false);
    }
  }

  async function createProfile(event: FormEvent) {
    event.preventDefault();
    const bandOrder = order
      .split(",")
      .map((value) => value.trim().toUpperCase())
      .filter(Boolean);
    setSaving(true);
    setSaved(false);
    setError(undefined);
    try {
      const profile = await api.createBandSequenceProfile({
        name,
        filename_prefix: prefix,
        capture_prefix: capturePrefix,
        band_order: bandOrder,
      });
      setProfiles((current) => [...current, profile]);
      setProfileId(profile.id);
      await api.assignBandSequenceProfile(dataset.id, profile.id);
      onSaved();
      setName("");
      setSaved(true);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Não foi possível criar a sequência",
      );
    } finally {
      setSaving(false);
    }
  }

  const activeProfile = profiles.find((profile) => profile.id === profileId);

  return (
    <section className="surface mt-8 p-6">
      <SectionHeading
        description="Confirme como os sufixos dos arquivos correspondem a Blue, Green, Red, Red Edge e NIR. A associação só é usada após sua escolha explícita."
        title="2. Revisão e sequência de bandas"
      />
      <div className="mt-5 rounded-lg border border-sky-200 bg-sky-50 p-4 text-sm text-sky-950">
        <strong>Por que revisar?</strong> Uma banda associada incorretamente
        produz um índice matematicamente válido, mas cientificamente incorreto.
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        <label className="grid min-w-72 flex-1 gap-2 text-sm font-medium">
          Sequência salva
          <select
            className="border-input rounded-md border px-3 py-2 text-sm"
            onChange={(event) => setProfileId(event.target.value)}
            value={profileId}
          >
            <option value="">Selecione uma sequência</option>
            {profiles.map((profile) => (
              <option key={profile.id} value={profile.id}>
                {profile.name} · {profile.band_order.join(" → ")}
              </option>
            ))}
          </select>
        </label>
        <Button
          className="self-end"
          disabled={saving || !profileId}
          onClick={() => void selectProfile()}
          variant="outline"
        >
          Usar sequência
        </Button>
      </div>
      {activeProfile && (
        <div className="mt-4 rounded-lg border p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm font-semibold">
              Ordem detectada nos arquivos
            </p>
            {dataset.band_sequence_profile_id === activeProfile.id && (
              <StatusBadge value="valid" />
            )}
          </div>
          <ol
            className="mt-3 flex flex-wrap items-center gap-2"
            aria-label="Ordem das bandas"
          >
            {activeProfile.band_order.map((band, index) => (
              <li
                className="flex items-center gap-2"
                key={`${band}-${String(index)}`}
              >
                <span className="bg-muted rounded-md px-3 py-2 text-xs font-semibold">
                  <span className="text-muted-foreground mr-1">
                    {index + 1}
                  </span>
                  {band.replace("_", " ")}
                </span>
                {index < activeProfile.band_order.length - 1 && (
                  <ArrowRight className="text-muted-foreground size-3" />
                )}
              </li>
            ))}
          </ol>
          <p className="text-muted-foreground mt-3 text-xs">
            Prefixo do arquivo: <code>{activeProfile.filename_prefix}</code> ·
            prefixo da captura: <code>{activeProfile.capture_prefix}</code>
          </p>
        </div>
      )}
      <form
        className="bg-muted/50 mt-6 grid gap-4 rounded-lg p-5 sm:grid-cols-2"
        onSubmit={(event) => void createProfile(event)}
      >
        <div className="sm:col-span-2">
          <h3 className="text-sm font-semibold">
            Criar sequência personalizada
          </h3>
          <p className="text-muted-foreground mt-1 text-xs">
            Use quando o padrão de nomes do seu sensor for diferente dos perfis
            existentes.
          </p>
        </div>
        <input
          className="border-input rounded-md border px-3 py-2"
          onChange={(event) => setName(event.target.value)}
          placeholder="Nome da sequência"
          required
          value={name}
        />
        <input
          className="border-input rounded-md border px-3 py-2"
          onChange={(event) => setPrefix(event.target.value)}
          placeholder="Prefixo do arquivo"
          required
          value={prefix}
        />
        <input
          className="border-input rounded-md border px-3 py-2"
          onChange={(event) => setCapturePrefix(event.target.value)}
          placeholder="Prefixo da repetição"
          required
          value={capturePrefix}
        />
        <input
          className="border-input rounded-md border px-3 py-2"
          onChange={(event) => setOrder(event.target.value)}
          placeholder="BLUE, GREEN, RED..."
          required
          value={order}
        />
        <div className="sm:col-span-2">
          <Button disabled={saving}>
            {saving ? "Salvando…" : "Salvar sequência"}
          </Button>
        </div>
      </form>
      {error && (
        <div className="mt-4">
          <ErrorState message={error} />
        </div>
      )}
      {saved && (
        <div className="mt-4 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-900">
          <strong>Sequência salva.</strong> As associações do dataset usarão
          esta configuração.
        </div>
      )}
    </section>
  );
}

function CaptureList({ datasetId }: { datasetId: string }) {
  const loader = useCallback(() => api.listCaptures(datasetId), [datasetId]);
  const [state, retry] = useData(loader, `captures-${datasetId}`);
  return (
    <section className="mt-10">
      <SectionHeading
        description={
          <>
            Abra uma repetição para desenhar a <TermHelp term="ROI" /> sobre a
            composição RGB e conferir o alinhamento nas bandas científicas.
          </>
        }
        title="3. Repetições e ROI"
      />
      <div className="mt-5">
        <State
          empty={state.status === "success" && state.data.length === 0}
          retry={retry}
          state={state}
        />
        {state.status === "success" && state.data.length === 0 && (
          <p className="text-muted-foreground -mt-12 pb-8 text-center text-xs">
            Se os uploads já foram validados, revise a sequência de bandas
            acima. Arquivos inválidos permanecem visíveis individualmente na
            etapa de upload.
          </p>
        )}
        {state.status === "success" && state.data.length > 0 && (
          <div className="grid gap-3 md:grid-cols-2">
            {state.data.map((capture) => (
              <button
                className="surface group hover:border-primary/30 hover:bg-muted/50 flex items-center justify-between p-5 text-left transition"
                key={capture.id}
                onClick={() => go(`/captures/${capture.id}/roi`)}
                type="button"
              >
                <span>
                  <strong>
                    {capture.replicate_label ?? "Repetição não rotulada"}
                  </strong>
                  <small className="text-muted-foreground mt-1 block">
                    {capture.capture_key} · {capture.band_count} banda(s) ·{" "}
                    {capture.roi_count} ROI(s)
                  </small>
                </span>
                <span className="bg-primary/10 text-primary grid size-10 place-items-center rounded-lg">
                  <ScanLine className="size-5" />
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function CaptureROIPage({ id }: { id: string }) {
  return (
    <Shell>
      <WorkflowStepper current={3} />
      <div className="desktop-recommended mb-4 items-start gap-3 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950">
        <ScanLine className="mt-0.5 size-4 shrink-0" />
        <p>
          <strong>Desktop recomendado.</strong> Para selecionar a folha com
          precisão, use uma tela maior e um dispositivo apontador.
        </p>
      </div>
      <ROIEditor captureId={id} />
    </Shell>
  );
}

export function App() {
  const path = usePath();
  const auth = useApplicationAuth();
  if (path === "/") return <LandingPage />;
  if (path === "/login") return <LoginPage />;
  if (auth.isLoading) {
    return (
      <main className="app-main">
        <LoadingState label="Verificando acesso seguro" />
      </main>
    );
  }
  if (!auth.isAuthenticated) return <LoginPage returnTo={path} />;
  if (path === "/dashboard") return <DashboardPage />;
  if (path === "/projects") return <ProjectsPage />;
  if (path === "/projects/new") return <NewProject />;
  const newDataset = /^\/projects\/([^/]+)\/datasets\/new$/.exec(path);
  if (newDataset?.[1] !== undefined) return <NewDataset id={newDataset[1]} />;
  const project = /^\/projects\/([^/]+)$/.exec(path);
  if (project?.[1] !== undefined) return <ProjectPage id={project[1]} />;
  const dataset = /^\/datasets\/([^/]+)$/.exec(path);
  if (dataset?.[1] !== undefined) return <DatasetPage id={dataset[1]} />;
  const processing = /^\/datasets\/([^/]+)\/processing$/.exec(path);
  if (processing?.[1] !== undefined)
    return (
      <Shell>
        <ProcessingPage datasetId={processing[1]} />
      </Shell>
    );
  const results = /^\/processing-jobs\/([^/]+)\/results$/.exec(path);
  if (results?.[1] !== undefined)
    return (
      <Shell>
        <ResultsPage jobId={results[1]} />
      </Shell>
    );
  const reproducibility = /^\/processing-jobs\/([^/]+)\/reproducibility$/.exec(
    path,
  );
  if (reproducibility?.[1] !== undefined)
    return (
      <Shell>
        <ResultsPage jobId={reproducibility[1]} initialTab="reproducibility" />
      </Shell>
    );
  const captureROI = /^\/captures\/([^/]+)\/roi$/.exec(path);
  if (captureROI?.[1] !== undefined)
    return <CaptureROIPage id={captureROI[1]} />;
  return (
    <Shell>
      <Heading title="Página não encontrada" />
      <Button onClick={() => go("/dashboard")}>Ir para dashboard</Button>
    </Shell>
  );
}
