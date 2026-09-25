import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { api } from "./api/client";
import {
  type AuthConfig,
  completeOidc,
  fetchAuthConfig,
  getToken,
  logout,
  onAuthChange,
  resumeOidc,
  adoptToken,
} from "./auth/session";
import { ScopeProvider } from "./api/scope";
import Audit from "./pages/Audit";
import ChangePassword from "./pages/ChangePassword";
import Settings from "./pages/Settings";
import Users from "./pages/Users";
import { Layout } from "./components/Layout";
import { useLiveEvents } from "./hooks/useLiveEvents";
import Analysis from "./pages/Analysis";
import AnalysisReport from "./pages/AnalysisReport";
import Assistant from "./pages/Assistant";
import Cameras from "./pages/Cameras";
import Dashboard from "./pages/Dashboard";
import LiveView from "./pages/LiveView";
import Login from "./pages/Login";
import Sessions from "./pages/Sessions";

function OidcCallback({ config }: { config: AuthConfig }) {
  const navigate = useNavigate();
  useEffect(() => {
    completeOidc(config)
      .then((path) => navigate(path, { replace: true }))
      .catch(() => navigate("/", { replace: true }));
  }, [config, navigate]);
  return <div className="p-8 text-sm text-muted">Completing sign-in…</div>;
}

/** Self-service password change, reachable from the header menu. */
function AccountPassword() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  return (
    <ChangePassword
      forced={false}
      onDone={(fresh) => {
        adoptToken(fresh);
        qc.invalidateQueries();
        navigate("/");
      }}
      onCancel={() => navigate("/")}
    />
  );
}

export default function App() {
  const qc = useQueryClient();
  const [token, setToken] = useState(getToken());
  const config = useQuery({ queryKey: ["auth-config"], queryFn: fetchAuthConfig, staleTime: Infinity });
  const me = useQuery({ queryKey: ["me", token], queryFn: api.me, enabled: !!token, retry: false });
  const connected = useLiveEvents();

  useEffect(() => onAuthChange(() => setToken(getToken())), []);
  useEffect(() => {
    if (config.data) resumeOidc(config.data);
  }, [config.data]);
  useEffect(() => {
    // any 401 from the API means the token is gone or expired: back to login
    const onUnauthorized = () => {
      logout(config.data);
      qc.clear();
    };
    window.addEventListener("ivaas:unauthorized", onUnauthorized);
    return () => window.removeEventListener("ivaas:unauthorized", onUnauthorized);
  }, [config.data, qc]);

  if (!config.data) return null;
  if (location.pathname === "/auth/callback") return <OidcCallback config={config.data} />;
  if (!token) return <Login config={config.data} />;
  if (me.isPending) return null;
  // The API refuses everything but /auth/me and /auth/password while a temporary
  // password is in force, so the portal must not pretend otherwise.
  if (me.data?.must_change_password) {
    return (
      <ChangePassword
        forced
        onDone={(fresh) => {
          adoptToken(fresh);
          qc.invalidateQueries();
        }}
      />
    );
  }

  return (
    // the whole shell shares one bay selection, header and pages alike
    <ScopeProvider>
      <Layout connected={connected} me={me.data} onLogout={() => logout(config.data)}>
        <Routes>
          <Route path="/" element={<Dashboard me={me.data} />} />
          <Route path="/live" element={<LiveView />} />
          <Route path="/sessions" element={<Sessions me={me.data} />} />
          <Route path="/cameras" element={<Cameras me={me.data} />} />
          <Route path="/audit" element={<Audit />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/users" element={<Users me={me.data?.subject ?? ""} />} />
          <Route path="/account/password" element={<AccountPassword />} />
          <Route path="/analysis" element={<Analysis me={me.data} />} />
          <Route path="/analysis/:id" element={<AnalysisReport />} />
          <Route path="/assistant" element={<Assistant />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Layout>
    </ScopeProvider>
  );
}
