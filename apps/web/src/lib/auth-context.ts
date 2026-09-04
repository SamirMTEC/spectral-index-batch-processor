import { type User } from "@auth0/auth0-react";
import { createContext, useContext } from "react";

export interface ApplicationAuth {
  enabled: boolean;
  isAuthenticated: boolean;
  isLoading: boolean;
  user: User | undefined;
  error: string | null;
  signIn: (returnTo?: string) => Promise<void>;
  signOut: () => Promise<void>;
}

export const defaultApplicationAuth: ApplicationAuth = {
  enabled: false,
  isAuthenticated: !import.meta.env.PROD,
  isLoading: false,
  user: undefined,
  error: null,
  signIn: () => Promise.resolve(),
  signOut: () => Promise.resolve(),
};

export const ApplicationAuthContext = createContext<ApplicationAuth>(
  defaultApplicationAuth,
);

export function useApplicationAuth() {
  return useContext(ApplicationAuthContext);
}
