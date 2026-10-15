import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { setUnauthorizedHandler } from "@/api/client";
import { keys, useMe } from "@/api/queries";
import { Layout } from "@/components/Layout";
import { Alert, Spinner } from "@/components/ui/primitives";
import { LoginPage } from "@/pages/LoginPage";

/** Shows the login page until /api/auth/me succeeds, then the app. */
export function AuthGate() {
  const qc = useQueryClient();
  const me = useMe();

  useEffect(() => {
    setUnauthorizedHandler(() => qc.setQueryData(keys.me, null));
  }, [qc]);

  if (me.isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner />
      </div>
    );
  }
  if (me.error) {
    return (
      <div className="p-8">
        <Alert tone="destructive">Can't reach the server: {me.error.message}</Alert>
      </div>
    );
  }
  if (!me.data) return <LoginPage />;
  return <Layout />;
}
