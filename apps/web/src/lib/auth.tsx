import { Auth0Provider, useAuth0, type AppState } from "@auth0/auth0-react";
import { useEffect, useLayoutEffect, type ReactNode } from "react";

import {
  ApplicationAuthContext,
  defaultApplicationAuth,
  type ApplicationAuth,
} from "@/lib/auth-context";
import { configureAccessTokenProvider } from "@/lib/api";

const domain = import.meta.env.VITE_AUTH0_DOMAIN?.trim();
const clientId = import.meta.env.VITE_AUTH0_CLIENT_ID?.trim();
const audience = import.meta.env.VITE_AUTH0_AUDIENCE?.trim();
// Bump when public authentication settings change so immutable CDN assets are invalidated.
const configurationRevision = "auth0-production-v1";
const providedValues = [domain, clientId, audience].filter(Boolean).length;
const isConfigured = providedValues === 3;
const configurationError =
  providedValues > 0 && !isConfigured
    ? "A configuração do Auth0 está incompleta."
    : import.meta.env.PROD && !isConfigured
      ? "A autenticação de produção ainda não foi configurada."
      : null;

const developmentAuth: ApplicationAuth = {
  ...defaultApplicationAuth,
  error: configurationError,
};

function Auth0Bridge({ children }: { children: ReactNode }) {
  const {
    error,
    getAccessTokenSilently,
    isAuthenticated,
    isLoading,
    loginWithRedirect,
    logout,
    user,
  } = useAuth0();

  useLayoutEffect(() => {
    configureAccessTokenProvider(() => getAccessTokenSilently());
    return () => configureAccessTokenProvider(null);
  }, [getAccessTokenSilently]);

  const value: ApplicationAuth = {
    enabled: true,
    isAuthenticated,
    isLoading,
    user,
    error: error?.message ?? null,
    signIn: async (returnTo = "/dashboard") => {
      await loginWithRedirect({ appState: { returnTo } });
    },
    signOut: async () => {
      await logout({
        logoutParams: { returnTo: window.location.origin },
      });
    },
  };

  return (
    <ApplicationAuthContext.Provider value={value}>
      {children}
    </ApplicationAuthContext.Provider>
  );
}

export function ApplicationAuthProvider({ children }: { children: ReactNode }) {
  useEffect(() => {
    if (!isConfigured) configureAccessTokenProvider(null);
  }, []);

  if (!isConfigured || !domain || !clientId || !audience) {
    return (
      <ApplicationAuthContext.Provider value={developmentAuth}>
        {children}
      </ApplicationAuthContext.Provider>
    );
  }

  const onRedirectCallback = (appState?: AppState) => {
    const returnTo = appState?.returnTo;
    window.history.replaceState(
      {},
      document.title,
      typeof returnTo === "string" ? returnTo : "/dashboard",
    );
    window.dispatchEvent(new PopStateEvent("popstate"));
  };

  return (
    <Auth0Provider
      authorizationParams={{
        audience,
        redirect_uri: window.location.origin,
      }}
      cacheLocation="memory"
      clientId={clientId}
      domain={domain}
      key={configurationRevision}
      onRedirectCallback={onRedirectCallback}
    >
      <Auth0Bridge>{children}</Auth0Bridge>
    </Auth0Provider>
  );
}
