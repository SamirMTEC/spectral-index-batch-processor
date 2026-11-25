using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace SpectralIndexDesktop
{
    internal sealed class DesktopConfig
    {
        public int version { get; set; }
        public string userId { get; set; }
        public string project { get; set; }
        public Dictionary<string, int> ports { get; set; }
        public Dictionary<string, string> secrets { get; set; }
    }

    internal sealed class DesktopWindow : Form
    {
        private readonly WebView2 browser = new WebView2();
        private readonly Label status = new Label();
        private readonly string installDir = AppDomain.CurrentDomain.BaseDirectory;
        private readonly string userDir = Environment.GetEnvironmentVariable("SPECTRAL_DESKTOP_USER_DATA") ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "@spectral-index", "desktop");
        private readonly JavaScriptSerializer json = new JavaScriptSerializer();
        private HttpListener server;
        private DesktopConfig config;
        private string envFile;
        private string origin;
        private bool closing;
        private readonly CancellationTokenSource shutdown = new CancellationTokenSource();

        public DesktopWindow()
        {
            Text = "SpectralIndex Desktop";
            Width = 1360;
            Height = 900;
            MinimumSize = new System.Drawing.Size(1000, 700);
            BackColor = System.Drawing.Color.FromArgb(245, 250, 247);
            status.Dock = DockStyle.Fill;
            status.TextAlign = System.Drawing.ContentAlignment.MiddleCenter;
            status.Font = new System.Drawing.Font("Segoe UI", 13);
            status.Text = "Iniciando o SpectralIndex Desktop...";
            browser.Dock = DockStyle.Fill;
            browser.Visible = false;
            Controls.Add(browser);
            Controls.Add(status);
            Shown += async (sender, args) => await StartAsync();
            FormClosing += OnClosing;
        }

        private void SetStatus(string message)
        {
            if (!IsDisposed && IsHandleCreated) BeginInvoke((Action)(() => status.Text = message));
        }

        private static int FreePort()
        {
            var listener = new TcpListener(IPAddress.Loopback, 0);
            listener.Start();
            int port = ((IPEndPoint)listener.LocalEndpoint).Port;
            listener.Stop();
            return port;
        }

        private static bool PortAvailable(int port)
        {
            if (port < 1 || port > 65535) return false;
            try
            {
                var listener = new TcpListener(IPAddress.Loopback, port);
                listener.Start();
                listener.Stop();
                return true;
            }
            catch (SocketException) { return false; }
        }

        private static string Secret()
        {
            var bytes = new byte[24];
            using (var rng = RandomNumberGenerator.Create()) rng.GetBytes(bytes);
            return Convert.ToBase64String(bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_');
        }

        private DesktopConfig LoadConfig()
        {
            Directory.CreateDirectory(userDir);
            string file = Path.Combine(userDir, "desktop.json");
            DesktopConfig value = null;
            if (File.Exists(file)) value = json.Deserialize<DesktopConfig>(File.ReadAllText(file, Encoding.UTF8));
            if (value == null || value.version != 1 || string.IsNullOrEmpty(value.userId) || string.IsNullOrEmpty(value.project) || value.ports == null || value.secrets == null)
            {
                value = new DesktopConfig
                {
                    version = 1,
                    userId = Guid.NewGuid().ToString(),
                    project = "spectral-desktop-" + Secret().Substring(0, 8).ToLowerInvariant(),
                    ports = new Dictionary<string, int>(),
                    secrets = new Dictionary<string, string> { { "postgres", Secret() }, { "storage", Secret() } }
                };
            }
            else
            {
                int apiPort;
                if (value.ports.TryGetValue("api", out apiPort) && !PortAvailable(apiPort))
                    throw new Exception("Os serviços deste perfil já estão em execução. Feche a outra edição do SpectralIndex Desktop antes de abrir esta versão.");
            }
            var assigned = new HashSet<int>();
            foreach (string key in new[] { "postgres", "redis", "storage", "storageConsole", "api" })
            {
                int port;
                if (!value.ports.TryGetValue(key, out port) || !PortAvailable(port) || !assigned.Add(port))
                {
                    do { port = FreePort(); } while (!assigned.Add(port));
                    value.ports[key] = port;
                }
            }
            File.WriteAllText(file, json.Serialize(value), new UTF8Encoding(false));
            return value;
        }

        private void WriteEnvironment()
        {
            var variables = new Dictionary<string, string>
            {
                { "POSTGRES_DB", "spectralindex" }, { "POSTGRES_USER", "spectralindex" },
                { "POSTGRES_PASSWORD", config.secrets["postgres"] }, { "POSTGRES_PORT", config.ports["postgres"].ToString() },
                { "REDIS_PORT", config.ports["redis"].ToString() }, { "MINIO_ROOT_USER", "spectraldesktop" },
                { "MINIO_ROOT_PASSWORD", config.secrets["storage"] }, { "MINIO_API_PORT", config.ports["storage"].ToString() },
                { "MINIO_CONSOLE_PORT", config.ports["storageConsole"].ToString() }, { "API_PORT", config.ports["api"].ToString() },
                { "SPECTRAL_ENVIRONMENT", "development" }, { "SPECTRAL_DEV_AUTH_ENABLED", "true" },
                { "DESKTOP_WEB_ORIGIN", origin }, { "DESKTOP_STORAGE_URL", "http://127.0.0.1:" + config.ports["storage"] }
            };
            envFile = Path.Combine(userDir, "stack.env");
            File.WriteAllLines(envFile, variables.Select(entry => entry.Key + "=" + entry.Value).ToArray(), Encoding.ASCII);
        }

        private string[] ComposeArgs(params string[] command)
        {
            return new[] { "compose", "--project-name", config.project, "--env-file", envFile,
                "-f", Path.Combine(installDir, "stack", "docker-compose.yml"),
                "-f", Path.Combine(installDir, "stack", "docker-compose.desktop.yml") }.Concat(command).ToArray();
        }

        private static string Quote(string value) { return "\"" + value.Replace("\"", "\\\"") + "\""; }

        private static async Task<string> DockerAsync(string[] args, int timeoutMs)
        {
            using (var process = new Process())
            {
                process.StartInfo = new ProcessStartInfo("docker", string.Join(" ", args.Select(Quote)))
                {
                    CreateNoWindow = true,
                    UseShellExecute = false,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    StandardOutputEncoding = Encoding.UTF8,
                    StandardErrorEncoding = Encoding.UTF8
                };
                process.Start();
                var output = process.StandardOutput.ReadToEndAsync();
                var errors = process.StandardError.ReadToEndAsync();
                if (!await Task.Run(() => process.WaitForExit(timeoutMs)))
                {
                    process.Kill();
                    throw new TimeoutException("O Docker demorou mais que o limite esperado.");
                }
                string result = (await output) + (await errors);
                if (process.ExitCode != 0) throw new Exception(result.Length > 3000 ? result.Substring(result.Length - 3000) : result);
                return result;
            }
        }

        private async Task StartStackAsync()
        {
            SetStatus("Verificando o Docker Desktop...");
            try { await DockerAsync(new[] { "info", "--format", "{{.ServerVersion}}" }, 20000); }
            catch { throw new Exception("Instale e inicie o Docker Desktop com contêineres Linux antes de abrir o SpectralIndex Desktop."); }
            var images = new[] { "spectral-index-desktop:0.6.0", "postgres:17-alpine", "redis:8-alpine", "minio/minio:RELEASE.2025-09-07T16-13-09Z" };
            bool missingImages = false;
            try { await DockerAsync(new[] { "image", "inspect" }.Concat(images).ToArray(), 20000); }
            catch { missingImages = true; }
            if (missingImages)
            {
                SetStatus("Preparando os serviços locais para uso offline. Isso pode levar alguns minutos.");
                await DockerAsync(new[] { "image", "load", "-i", Path.Combine(installDir, "offline-images.tar.gz") }, 1800000);
                await DockerAsync(new[] { "image", "inspect" }.Concat(images).ToArray(), 20000);
            }
            SetStatus("Iniciando os serviços locais...");
            await DockerAsync(ComposeArgs("up", "-d", "--no-build", "--pull", "never"), 300000);
            var deadline = DateTime.UtcNow.AddMinutes(4);
            using (var client = new WebClient())
            {
                while (DateTime.UtcNow < deadline)
                {
                    try { if (client.DownloadString("http://127.0.0.1:" + config.ports["api"] + "/ready") != null) return; }
                    catch (WebException) { }
                    SetStatus("Aguardando banco, armazenamento e API local...");
                    await Task.Delay(2000);
                }
            }
            throw new TimeoutException("A API local não ficou pronta em quatro minutos. Verifique os serviços no Docker Desktop.");
        }

        private async Task StartAsync()
        {
            try
            {
                config = LoadConfig();
                origin = "http://127.0.0.1:" + FreePort();
                WriteEnvironment();
                server = new HttpListener();
                server.Prefixes.Add(origin + "/");
                server.Start();
                ServeAsync();
                await browser.EnsureCoreWebView2Async();
                browser.CoreWebView2.Settings.AreDevToolsEnabled = false;
                browser.CoreWebView2.Settings.AreDefaultContextMenusEnabled = false;
                browser.CoreWebView2.NewWindowRequested += (sender, args) => args.Handled = true;
                browser.CoreWebView2.NavigationStarting += (sender, args) => { if (!args.Uri.StartsWith(origin + "/", StringComparison.OrdinalIgnoreCase)) args.Cancel = true; };
                await StartStackAsync();
                if (shutdown.IsCancellationRequested) return;
                browser.Source = new Uri(origin + "/dashboard");
                status.Visible = false;
                browser.Visible = true;
            }
            catch (Exception error)
            {
                SetStatus("Falha ao iniciar: " + error.Message);
                MessageBox.Show(this, error.Message, "SpectralIndex Desktop não iniciou", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        private async Task ServeAsync()
        {
            while (server != null && server.IsListening)
            {
                HttpListenerContext context;
                try { context = await server.GetContextAsync(); }
                catch (HttpListenerException) { break; }
                catch (ObjectDisposedException) { break; }
                Task.Run(() => HandleRequest(context));
            }
        }

        private void HandleRequest(HttpListenerContext context)
        {
            try
            {
                var request = context.Request;
                var response = context.Response;
                response.Headers["X-Content-Type-Options"] = "nosniff";
                response.Headers["Referrer-Policy"] = "no-referrer";
                response.Headers["Content-Security-Policy"] = "default-src 'self'; base-uri 'self'; object-src 'none'; form-action 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' http://127.0.0.1:" + config.ports["storage"] + "; img-src 'self' blob: data: http://127.0.0.1:" + config.ports["storage"] + "; font-src 'self' data:";
                string path = request.Url.AbsolutePath;
                if (path == "/desktop-identity")
                {
                    response.ContentType = "application/json; charset=utf-8";
                    response.Headers["Cache-Control"] = "no-store";
                    byte[] bytes = Encoding.UTF8.GetBytes(json.Serialize(new { userId = config.userId }));
                    response.OutputStream.Write(bytes, 0, bytes.Length);
                }
                else if (path.StartsWith("/api/v1/", StringComparison.Ordinal)) Proxy(context);
                else ServeFile(context, path);
            }
            catch (Exception)
            {
                try { context.Response.StatusCode = 500; } catch { }
            }
            finally { try { context.Response.Close(); } catch { } }
        }

        private void Proxy(HttpListenerContext context)
        {
            var request = context.Request;
            var response = context.Response;
            if (request.Headers["Origin"] != null && request.Headers["Origin"] != origin) { response.StatusCode = 403; return; }
            var upstream = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:" + config.ports["api"] + request.RawUrl);
            upstream.Method = request.HttpMethod;
            upstream.AllowAutoRedirect = false;
            upstream.Timeout = 300000;
            upstream.ReadWriteTimeout = 300000;
            upstream.ContentType = request.ContentType;
            foreach (string header in new[] { "Authorization", "X-Dev-User-Id", "Idempotency-Key", "If-Match", "X-Request-ID" })
                if (request.Headers[header] != null) upstream.Headers[header] = request.Headers[header];
            if (request.HasEntityBody)
            {
                upstream.ContentLength = request.ContentLength64;
                using (var stream = upstream.GetRequestStream()) request.InputStream.CopyTo(stream);
            }
            HttpWebResponse remote;
            try { remote = (HttpWebResponse)upstream.GetResponse(); }
            catch (WebException error) { remote = error.Response as HttpWebResponse; if (remote == null) { response.StatusCode = 502; return; } }
            using (remote)
            {
                response.StatusCode = (int)remote.StatusCode;
                response.ContentType = remote.ContentType;
                foreach (string header in new[] { "Content-Disposition", "ETag", "Cache-Control", "X-Request-ID" })
                    if (remote.Headers[header] != null) response.Headers[header] = remote.Headers[header];
                using (var stream = remote.GetResponseStream()) stream.CopyTo(response.OutputStream);
            }
        }

        private void ServeFile(HttpListenerContext context, string requestPath)
        {
            var request = context.Request;
            var response = context.Response;
            if (request.HttpMethod != "GET" && request.HttpMethod != "HEAD") { response.StatusCode = 405; return; }
            string root = Path.GetFullPath(Path.Combine(installDir, "web"));
            string name = Uri.UnescapeDataString(requestPath).TrimStart('/').Replace('/', Path.DirectorySeparatorChar);
            string file = Path.GetFullPath(Path.Combine(root, name));
            if (!file.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase) && file != root) { response.StatusCode = 403; return; }
            if (!File.Exists(file))
            {
                if (Path.HasExtension(file)) { response.StatusCode = 404; return; }
                file = Path.Combine(root, "index.html");
            }
            string extension = Path.GetExtension(file).ToLowerInvariant();
            var types = new Dictionary<string, string> { { ".html", "text/html; charset=utf-8" }, { ".js", "text/javascript; charset=utf-8" }, { ".css", "text/css; charset=utf-8" }, { ".json", "application/json" }, { ".svg", "image/svg+xml" }, { ".png", "image/png" }, { ".ico", "image/x-icon" }, { ".woff", "font/woff" }, { ".woff2", "font/woff2" } };
            response.ContentType = types.ContainsKey(extension) ? types[extension] : "application/octet-stream";
            response.Headers["Cache-Control"] = Path.GetFileName(file) == "index.html" ? "no-store" : "public, max-age=31536000, immutable";
            if (request.HttpMethod == "GET") using (var stream = File.OpenRead(file)) stream.CopyTo(response.OutputStream);
        }

        private async void OnClosing(object sender, FormClosingEventArgs args)
        {
            if (closing) return;
            closing = true;
            shutdown.Cancel();
            if (server != null) server.Stop();
            if (config == null || envFile == null) return;
            args.Cancel = true;
            status.Text = "Encerrando os serviços locais...";
            status.Visible = true;
            browser.Visible = false;
            try { await DockerAsync(ComposeArgs("stop", "--timeout", "30"), 60000); } catch { }
            Close();
        }
    }

    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            bool first;
            using (var mutex = new Mutex(true, "SpectralIndexDesktop-SingleInstance", out first))
            {
                if (!first) return;
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new DesktopWindow());
            }
        }
    }
}
