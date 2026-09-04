import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "@/app";
import { ApplicationAuthProvider } from "@/lib/auth";
import "@/styles.css";

const root = document.getElementById("root");

if (root === null) {
  throw new Error("Root element not found");
}

createRoot(root).render(
  <StrictMode>
    <ApplicationAuthProvider>
      <App />
    </ApplicationAuthProvider>
  </StrictMode>,
);
