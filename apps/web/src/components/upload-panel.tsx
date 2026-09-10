import { Archive, CloudUpload, FolderOpen, RotateCw, X } from "lucide-react";
import {
  type ChangeEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import { Button } from "@/components/ui/button";
import {
  EmptyState,
  ErrorState,
  InlineLoader,
  SectionHeading,
  StatusBadge,
} from "@/components/product-ui";
import {
  api,
  type UploadMode,
  type UploadObject,
  type UploadSession,
} from "@/lib/api";

const acceptedImages = ".tif,.tiff,.png,.jpg,.jpeg";
const terminalStates = new Set(["valid", "invalid", "failed"]);

interface SelectedFile {
  file: File;
  relativePath: string;
}

function fileForUpload(
  files: Map<string, File>,
  serverPath: string,
): File | undefined {
  const exact = files.get(serverPath);
  if (exact) return exact;

  // The backend canonically stores paths with POSIX separators and NFC Unicode.
  // `webkitRelativePath` can differ on Windows, so match the canonical spelling
  // before falling back to a unique filename match.
  const canonicalPath = serverPath.replaceAll("\\", "/").normalize("NFC");
  const canonical = files.get(canonicalPath);
  if (canonical) return canonical;

  const filename = canonicalPath.split("/").at(-1);
  if (!filename) return undefined;
  const candidates = [...files.entries()].filter(([path]) =>
    path.replaceAll("\\", "/").normalize("NFC").endsWith(`/${filename}`),
  );
  return candidates.length === 1 ? candidates[0]?.[1] : undefined;
}

function humanSize(bytes: number) {
  if (bytes < 1024) return `${String(bytes)} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

function putBlob(
  url: string,
  blob: Blob,
  contentType: string,
  signal: AbortSignal,
  onProgress: (loaded: number) => void,
) {
  return new Promise<string>((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("PUT", url);
    request.setRequestHeader(
      "Content-Type",
      contentType || "application/octet-stream",
    );
    request.upload.addEventListener("progress", (event) =>
      onProgress(event.loaded),
    );
    request.addEventListener("load", () => {
      if (request.status >= 200 && request.status < 300) {
        resolve(request.getResponseHeader("ETag")?.replaceAll('"', "") ?? "");
      } else {
        reject(
          new Error(`Storage rejected the upload (${String(request.status)})`),
        );
      }
    });
    request.addEventListener("error", () =>
      reject(new Error("Storage connection failed")),
    );
    request.addEventListener("abort", () =>
      reject(new DOMException("Aborted", "AbortError")),
    );
    signal.addEventListener("abort", () => request.abort(), { once: true });
    request.send(blob);
  });
}

export function UploadPanel({ datasetId }: { datasetId: string }) {
  const [sessions, setSessions] = useState<UploadSession[]>([]);
  const [progress, setProgress] = useState<Record<string, number>>({});
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const folderInput = useRef<HTMLInputElement>(null);
  const filesByPath = useRef(new Map<string, File>());
  const controllers = useRef(new Map<string, AbortController>());

  const refresh = useCallback(async () => {
    try {
      setSessions(await api.listUploads(datasetId));
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Falha ao consultar uploads",
      );
    } finally {
      setLoading(false);
    }
  }, [datasetId]);

  useEffect(() => {
    folderInput.current?.setAttribute("webkitdirectory", "");
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const active = sessions.some((session) =>
      session.objects.some((item) => !terminalStates.has(item.state)),
    );
    if (!active) return;
    const timer = window.setInterval(() => void refresh(), 2_500);
    return () => window.clearInterval(timer);
  }, [refresh, sessions]);

  function replaceObject(sessionId: string, value: UploadObject) {
    setSessions((current) =>
      current.map((session) =>
        session.id === sessionId
          ? {
              ...session,
              objects: session.objects.map((item) =>
                item.id === value.id ? value : item,
              ),
            }
          : session,
      ),
    );
  }

  async function uploadObject(
    session: UploadSession,
    item: UploadObject,
    file: File,
  ) {
    const controller = new AbortController();
    controllers.current.set(item.id, controller);
    // The API signs the object with this exact content type. Browsers often infer
    // `image/tiff` for TIFF files; sending that inferred type would invalidate the
    // S3/MinIO signature even though the file itself is valid.
    const type = "application/octet-stream";
    try {
      if (item.method === "single" && item.upload_url) {
        await putBlob(
          item.upload_url,
          file,
          type,
          controller.signal,
          (loaded) =>
            setProgress((current) => ({
              ...current,
              [item.id]: (loaded / file.size) * 100,
            })),
        );
        replaceObject(
          session.id,
          await api.completeUpload(session.id, item.id, []),
        );
        return;
      }
      if (item.method !== "multipart" || !item.part_size || !item.part_count)
        return;
      const partNumbers = Array.from(
        { length: item.part_count },
        (_, index) => index + 1,
      );
      const completed: { part_number: number; etag: string }[] = [];
      let completedBytes = 0;
      for (let offset = 0; offset < partNumbers.length; offset += 100) {
        const requested = partNumbers.slice(offset, offset + 100);
        const signed = await api.getUploadPartUrls(
          session.id,
          item.id,
          requested,
        );
        for (const part of signed.parts) {
          const start = (part.part_number - 1) * item.part_size;
          const chunk = file.slice(
            start,
            Math.min(start + item.part_size, file.size),
          );
          const etag = await putBlob(
            part.upload_url,
            chunk,
            type,
            controller.signal,
            (loaded) =>
              setProgress((current) => ({
                ...current,
                [item.id]: ((completedBytes + loaded) / file.size) * 100,
              })),
          );
          completedBytes += chunk.size;
          completed.push({ part_number: part.part_number, etag });
        }
      }
      replaceObject(
        session.id,
        await api.completeUpload(session.id, item.id, completed),
      );
    } catch (cause) {
      if (!controller.signal.aborted) {
        replaceObject(
          session.id,
          await api.markUploadFailed(session.id, item.id),
        );
        setError(
          cause instanceof Error ? cause.message : "Upload interrompido",
        );
      }
    } finally {
      controllers.current.delete(item.id);
    }
  }

  async function begin(mode: UploadMode, selected: SelectedFile[]) {
    if (!selected.length) return;
    setBusy(true);
    setError(undefined);
    for (const item of selected)
      filesByPath.current.set(item.relativePath, item.file);
    try {
      const session = await api.createUpload(
        datasetId,
        mode,
        selected.map((item) => ({
          relative_path: item.relativePath,
          size_bytes: item.file.size,
          content_type: item.file.type,
        })),
      );
      setSessions((current) => [session, ...current]);
      const queue = session.objects.filter(
        (item) => item.state === "uploading",
      );
      let cursor = 0;
      const workers = Array.from(
        { length: Math.min(4, queue.length) },
        async () => {
          while (cursor < queue.length) {
            const item = queue[cursor++];
            if (!item) continue;
            const file = fileForUpload(
              filesByPath.current,
              item.client_relative_path,
            );
            if (!file) {
              setError(
                `Não foi possível localizar ${item.client_relative_path} na seleção local. Selecione a pasta novamente.`,
              );
              replaceObject(
                session.id,
                await api.markUploadFailed(session.id, item.id),
              );
              continue;
            }
            await uploadObject(session, item, file);
          }
        },
      );
      await Promise.all(workers);
      await refresh();
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Não foi possível iniciar o upload",
      );
    } finally {
      setBusy(false);
    }
  }

  function select(mode: UploadMode, event: ChangeEvent<HTMLInputElement>) {
    const files = [...(event.target.files ?? [])];
    const selected = files.map((file) => ({
      file,
      relativePath:
        mode === "directory" && file.webkitRelativePath
          ? file.webkitRelativePath
          : file.name,
    }));
    event.target.value = "";
    void begin(mode, selected);
  }

  async function retry(session: UploadSession, item: UploadObject) {
    const file = fileForUpload(filesByPath.current, item.client_relative_path);
    if (!file) {
      setError(
        "Selecione novamente o arquivo para tentar após recarregar a página.",
      );
      return;
    }
    try {
      const plan = await api.retryUpload(session.id, item.id);
      replaceObject(session.id, plan);
      await uploadObject(session, plan, file);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Retry falhou");
    }
  }

  async function cancel(session: UploadSession) {
    for (const item of session.objects)
      controllers.current.get(item.id)?.abort();
    try {
      const cancelled = await api.cancelUpload(session.id);
      setSessions((current) =>
        current.map((value) => (value.id === cancelled.id ? cancelled : value)),
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Cancelamento falhou");
    }
  }

  return (
    <section className="surface mt-10 p-6">
      <SectionHeading
        description="Envie TIFFs científicos diretamente ao storage. PNG e JPEG são aceitos apenas como referência visual; os originais nunca são alterados."
        title="1. Upload de imagens"
      />
      <div className="mt-5 grid gap-3 sm:grid-cols-3">
        <div className="rounded-lg border p-4">
          <CloudUpload className="text-primary size-5" />
          <h3 className="mt-3 text-sm font-semibold">Arquivos individuais</h3>
          <p className="text-muted-foreground mt-1 min-h-10 text-xs leading-5">
            Selecione TIFFs ou imagens auxiliares específicas.
          </p>
          <UploadButton
            accept={acceptedImages}
            disabled={busy}
            icon={<CloudUpload />}
            label="Selecionar arquivos"
            onChange={(event) => select("files", event)}
          />
        </div>
        <div className="rounded-lg border p-4">
          <FolderOpen className="text-primary size-5" />
          <h3 className="mt-3 text-sm font-semibold">Pasta completa</h3>
          <p className="text-muted-foreground mt-1 min-h-10 text-xs leading-5">
            Preserva os caminhos relativos para lotes organizados.
          </p>
          <label>
            <input
              className="sr-only"
              multiple
              onChange={(event) => select("directory", event)}
              ref={folderInput}
              type="file"
            />
            <Button
              asChild
              className="mt-3 w-full"
              disabled={busy}
              variant="outline"
            >
              <span>
                <FolderOpen />
                Selecionar pasta
              </span>
            </Button>
          </label>
        </div>
        <div className="rounded-lg border p-4">
          <Archive className="text-primary size-5" />
          <h3 className="mt-3 text-sm font-semibold">Arquivo ZIP</h3>
          <p className="text-muted-foreground mt-1 min-h-10 text-xs leading-5">
            O conteúdo será validado e cada erro ficará isolado.
          </p>
          <UploadButton
            accept=".zip"
            disabled={busy}
            icon={<Archive />}
            label="Selecionar ZIP"
            onChange={(event) => select("zip", event)}
          />
        </div>
      </div>
      <p className="text-muted-foreground mt-4 text-xs">
        Formatos científicos: .tif e .tiff · Checksum SHA-256 calculado para
        rastreabilidade · Upload direto, sem passagem desnecessária pela API.
      </p>
      {error && (
        <div className="mt-4">
          <ErrorState message={error} />
        </div>
      )}
      {busy && (
        <div className="mt-4">
          <InlineLoader label="Transferindo até quatro arquivos em paralelo…" />
        </div>
      )}
      {!loading && sessions.length === 0 && (
        <div className="mt-6">
          <EmptyState
            description="Escolha arquivos, uma pasta ou um ZIP. O progresso e a validação de cada item aparecerão aqui."
            icon="image"
            title="Nenhum upload iniciado"
          />
        </div>
      )}
      <div className="mt-6 space-y-5">
        {sessions.map((session) => (
          <article
            className="border-border bg-card overflow-hidden rounded-xl border"
            key={session.id}
          >
            <header className="bg-muted/40 flex items-center justify-between gap-4 px-4 py-3 text-sm">
              <span className="font-medium">
                {modeLabel(session.mode)} · {session.total_files} arquivo(s) ·{" "}
                {humanSize(session.total_bytes)}
              </span>
              {session.state === "uploading" && (
                <Button
                  onClick={() => void cancel(session)}
                  size="sm"
                  variant="ghost"
                >
                  <X />
                  Cancelar
                </Button>
              )}
            </header>
            <div className="divide-border divide-y">
              {session.objects.map((item) => {
                const percent =
                  item.state === "valid" ? 100 : (progress[item.id] ?? 0);
                return (
                  <div
                    className="grid gap-2 px-4 py-3 text-sm md:grid-cols-[1fr_130px_110px_auto] md:items-center"
                    key={item.id}
                  >
                    <div className="min-w-0">
                      <p
                        className="truncate font-medium"
                        title={item.client_relative_path}
                      >
                        {item.client_relative_path}
                      </p>
                      <p className="text-muted-foreground">
                        {humanSize(item.declared_size)} · {item.role}
                      </p>
                    </div>
                    <div>
                      <div className="bg-muted h-2 overflow-hidden rounded-full">
                        <div
                          className="bg-primary h-full transition-all"
                          style={{
                            width: `${String(Math.min(100, percent))}%`,
                          }}
                        />
                      </div>
                      <span className="text-muted-foreground text-xs">
                        {Math.round(percent)}%
                      </span>
                    </div>
                    <StatusBadge value={item.state} />
                    {item.state === "failed" ? (
                      <Button
                        onClick={() => void retry(session, item)}
                        size="sm"
                        variant="outline"
                      >
                        <RotateCw />
                        Repetir
                      </Button>
                    ) : (
                      <span className="text-muted-foreground max-w-48 text-xs">
                        {item.failure_message ??
                          (item.verified_checksum
                            ? `SHA-256 ${item.verified_checksum.slice(0, 12)}…`
                            : "")}
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function UploadButton({
  accept,
  disabled,
  icon,
  label,
  onChange,
}: {
  accept: string;
  disabled: boolean;
  icon: React.ReactNode;
  label: string;
  onChange: (event: ChangeEvent<HTMLInputElement>) => void;
}) {
  return (
    <label>
      <input
        accept={accept}
        className="sr-only"
        disabled={disabled}
        multiple
        onChange={onChange}
        type="file"
      />
      <Button
        asChild
        className="mt-3 w-full"
        disabled={disabled}
        variant="outline"
      >
        <span>
          {icon}
          {label}
        </span>
      </Button>
    </label>
  );
}

function modeLabel(mode: UploadMode) {
  return mode === "files" ? "Arquivos" : mode === "directory" ? "Pasta" : "ZIP";
}
