import {
  AlertCircle,
  Check,
  CheckCircle2,
  Circle,
  CloudUpload,
  FileChartColumn,
  FolderKanban,
  HelpCircle,
  Image,
  Layers3,
  LoaderCircle,
  Play,
  ScanLine,
  SlidersHorizontal,
} from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";

const termDefinitions = {
  ROI: "Região de interesse: a área da imagem que será incluída nos cálculos.",
  NDVI: "Índice de vegetação que combina as bandas NIR e Red para representar vigor vegetal.",
  GNDVI:
    "Variação do NDVI que usa Green e NIR, sensível a diferenças de clorofila.",
  SAVI: "Índice que reduz a influência do solo usando um fator de ajuste L.",
  CIgreen:
    "Índice de clorofila que relaciona NIR e Green; valores inválidos e divisão por zero são excluídos.",
  "CIred-edge":
    "Índice de clorofila que relaciona NIR e Red Edge, preservando NoData e pixels inválidos.",
  NDRE: "Índice normalizado com NIR e RedEdge, útil para avaliar vigor e clorofila em vegetação mais desenvolvida.",
  MCARI:
    "Índice de absorção de clorofila cuja precisão depende da posição espectral real da banda RedEdge do sensor.",
  VARI: "Índice de vegetação calculado apenas com as bandas visíveis Green, Red e Blue.",
  ExG: "Excesso de verde: realça a contribuição do canal Green em imagens RGB.",
  NoData:
    "Valor reservado para pixels sem medição válida; esses pixels não entram nas estatísticas.",
  reflectance:
    "Reflectância: proporção da radiação refletida pelo alvo após calibração radiométrica.",
  DN: "Digital Number: valor digital bruto registrado pelo sensor, antes da conversão para reflectância.",
} as const;

export function TermHelp({
  term,
  label,
}: {
  term: keyof typeof termDefinitions;
  label?: string;
}) {
  return (
    <span className="term-help">
      {label ?? term}
      <button
        aria-label={`O que significa ${term}?`}
        className="term-help__trigger"
        type="button"
      >
        <HelpCircle aria-hidden="true" />
      </button>
      <span className="term-help__content" role="tooltip">
        {termDefinitions[term]}
      </span>
    </span>
  );
}

const workflow = [
  { label: "Upload", icon: CloudUpload },
  { label: "Bandas", icon: Layers3 },
  { label: "ROI", icon: ScanLine },
  { label: "Índices", icon: SlidersHorizontal },
  { label: "Processar", icon: Play },
  { label: "Resultados", icon: FileChartColumn },
] as const;
const desktopWorkflow = [
  workflow[0],
  workflow[1],
  { label: "Calibração", icon: SlidersHorizontal },
  workflow[2],
  workflow[3],
  workflow[4],
  workflow[5],
] as const;

export function WorkflowStepper({
  current,
  datasetId,
  resultsHref,
}: {
  current: number;
  datasetId?: string | undefined;
  resultsHref?: string | undefined;
}) {
  const desktop = import.meta.env.VITE_DESKTOP_LOCAL === "true";
  const visibleWorkflow = desktop ? desktopWorkflow : workflow;
  return (
    <nav aria-label="Etapas da análise" className="workflow-stepper">
      <div className="workflow-stepper__heading">
        <span>Fluxo da análise</span>
        <small>Etapa {current} de {visibleWorkflow.length}</small>
      </div>
      <ol style={desktop ? { gridTemplateColumns: "repeat(7, minmax(7rem, 1fr))", minWidth: "55rem" } : undefined}>
        {visibleWorkflow.map((step, index) => {
          const number = index + 1;
          const Icon = step.icon;
          const state =
            number < current
              ? "complete"
              : number === current
                ? "current"
                : "upcoming";
          const href = desktop ? (
            datasetId && number <= 2
              ? `/datasets/${datasetId}${number === 1 ? "#upload" : "#bands"}`
              : datasetId && number === 3
                ? `/datasets/${datasetId}/calibration`
                : datasetId && number === 4
                  ? `/datasets/${datasetId}#captures`
                  : datasetId && (number === 5 || number === 6)
                    ? `/datasets/${datasetId}/processing`
                    : number === 7 ? resultsHref : undefined
          ) :
            datasetId && number <= 3
              ? `/datasets/${datasetId}${number === 1 ? "#upload" : number === 2 ? "#bands" : "#captures"}`
              : datasetId && (number === 4 || number === 5)
                ? `/datasets/${datasetId}/processing`
                : number === 6
                  ? resultsHref
                  : undefined;
          const content = (
            <>
              <span className="workflow-stepper__icon">
                {state === "complete" ? (
                  <Check aria-hidden="true" />
                ) : (
                  <Icon aria-hidden="true" />
                )}
              </span>
              <span>
                <small>{number}</small>
                {step.label}
              </span>
            </>
          );
          return (
            <li
              className={`workflow-stepper__step is-${state}`}
              key={step.label}
            >
              {href ? <a href={href}>{content}</a> : <div>{content}</div>}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

export function PageHeader({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow?: string;
  title: string;
  description?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h1>{title}</h1>
        {description && (
          <div className="page-header__description">{description}</div>
        )}
      </div>
      {action && <div className="page-header__actions">{action}</div>}
    </header>
  );
}

export function SectionHeading({
  title,
  description,
  action,
}: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="section-heading">
      <div>
        <h2>{title}</h2>
        {description && <p>{description}</p>}
      </div>
      {action}
    </div>
  );
}

export function LoadingState({
  label = "Carregando dados…",
}: {
  label?: string;
}) {
  return (
    <div aria-busy="true" aria-live="polite" className="loading-panel">
      <div className="skeleton h-4 w-28" />
      <div className="skeleton h-20 w-full" />
      <div className="skeleton h-20 w-full" />
      <span className="sr-only">{label}</span>
    </div>
  );
}

export function InlineLoader({ label }: { label: string }) {
  return (
    <p aria-live="polite" className="inline-loader">
      <LoaderCircle aria-hidden="true" className="animate-spin" /> {label}
    </p>
  );
}

export function EmptyState({
  title,
  description,
  action,
  icon = "folder",
}: {
  title: string;
  description: string;
  action?: ReactNode;
  icon?: "folder" | "image" | "results";
}) {
  const Icon =
    icon === "image"
      ? Image
      : icon === "results"
        ? FileChartColumn
        : FolderKanban;
  return (
    <div className="empty-state">
      <span className="empty-state__icon">
        <Icon aria-hidden="true" />
      </span>
      <h3>{title}</h3>
      <p>{description}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({
  message,
  retry,
}: {
  message: string;
  retry?: () => void;
}) {
  return (
    <div className="alert alert--error" role="alert">
      <AlertCircle aria-hidden="true" />
      <div>
        <strong>Não foi possível carregar esta área</strong>
        <p>{message}</p>
      </div>
      {retry && (
        <Button onClick={retry} size="sm" variant="outline">
          Tentar novamente
        </Button>
      )}
    </div>
  );
}

export function SuccessState({ children }: { children: ReactNode }) {
  return (
    <div className="alert alert--success" role="status">
      <CheckCircle2 aria-hidden="true" />
      <div>{children}</div>
    </div>
  );
}

export function StatusBadge({ value }: { value: string }) {
  const map: Record<string, { label: string; tone: string }> = {
    active: { label: "Ativo", tone: "success" },
    archived: { label: "Arquivado", tone: "neutral" },
    draft: { label: "Rascunho", tone: "neutral" },
    pending: { label: "Pendente", tone: "neutral" },
    uploading: { label: "Enviando", tone: "info" },
    uploaded: { label: "Enviado", tone: "info" },
    validating: { label: "Validando", tone: "info" },
    valid: { label: "Válido", tone: "success" },
    ready: { label: "Pronto", tone: "success" },
    needs_review: { label: "Revisão necessária", tone: "warning" },
    invalid: { label: "Inválido", tone: "danger" },
    failed: { label: "Falhou", tone: "danger" },
    queued: { label: "Na fila", tone: "info" },
    running: { label: "Em processamento", tone: "info" },
    completed: { label: "Concluído", tone: "success" },
    completed_with_errors: { label: "Concluído com erros", tone: "warning" },
    cancelled: { label: "Cancelado", tone: "neutral" },
    skipped: { label: "Ignorado", tone: "warning" },
  };
  const status = map[value] ?? {
    label: value.replaceAll("_", " "),
    tone: "neutral",
  };
  return (
    <span className={`status-badge status-badge--${status.tone}`}>
      <Circle aria-hidden="true" /> {status.label}
    </span>
  );
}
