"use strict";

const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");

const mime = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".ico": "image/x-icon",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
};

function createDesktopServer({ webDir, apiPort, storagePort, userId }) {
  const root = path.resolve(webDir);
  const csp = [
    "default-src 'self'",
    "base-uri 'self'",
    "object-src 'none'",
    "form-action 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    `connect-src 'self' http://127.0.0.1:${storagePort}`,
    `img-src 'self' blob: data: http://127.0.0.1:${storagePort}`,
    "font-src 'self' data:",
  ].join("; ");

  const server = http.createServer((request, response) => {
    const pathname = new URL(request.url || "/", "http://127.0.0.1").pathname;
    response.setHeader("Content-Security-Policy", csp);
    response.setHeader("X-Content-Type-Options", "nosniff");
    response.setHeader("Referrer-Policy", "no-referrer");

    if (pathname === "/desktop-identity") {
      response.writeHead(200, {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
      });
      response.end(JSON.stringify({ userId }));
      return;
    }

    if (pathname.startsWith("/api/v1/")) {
      const expectedOrigin = `http://127.0.0.1:${server.address().port}`;
      if (request.headers.origin && request.headers.origin !== expectedOrigin) {
        response.writeHead(403);
        response.end();
        return;
      }
      const upstream = http.request(
        {
          hostname: "127.0.0.1",
          port: apiPort,
          path: request.url,
          method: request.method,
          headers: { ...request.headers, host: `127.0.0.1:${apiPort}` },
        },
        (upstreamResponse) => {
          response.writeHead(upstreamResponse.statusCode || 502, upstreamResponse.headers);
          upstreamResponse.pipe(response);
        },
      );
      upstream.on("error", () => {
        if (!response.headersSent) response.writeHead(502);
        response.end("A API local não está disponível.");
      });
      request.pipe(upstream);
      return;
    }

    if (request.method !== "GET" && request.method !== "HEAD") {
      response.writeHead(405);
      response.end();
      return;
    }

    let filename;
    try {
      filename = decodeURIComponent(pathname);
    } catch {
      response.writeHead(400);
      response.end();
      return;
    }
    const candidate = path.resolve(root, `.${filename}`);
    if (candidate !== root && !candidate.startsWith(`${root}${path.sep}`)) {
      response.writeHead(403);
      response.end();
      return;
    }
    let file = candidate;
    if (!fs.existsSync(file) || !fs.statSync(file).isFile()) {
      if (path.extname(filename)) {
        response.writeHead(404);
        response.end();
        return;
      }
      file = path.join(root, "index.html");
    }
    response.setHeader("Content-Type", mime[path.extname(file)] || "application/octet-stream");
    response.setHeader(
      "Cache-Control",
      path.basename(file) === "index.html" ? "no-store" : "public, max-age=31536000, immutable",
    );
    response.writeHead(200);
    if (request.method === "HEAD") response.end();
    else fs.createReadStream(file).pipe(response);
  });
  return server;
}

module.exports = { createDesktopServer };
