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
  switchBreakGlass,
  can,
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
import { LiveActivityProvider } from "./live/provider";
import Alerts from "./pages/Alerts";
import Security from "./pages/Security";
import Cameras from "./pages/Cameras";
import EdgeNodes from "./pages/EdgeNodes";
import Fleet from "./pages/Fleet";
import Balances from "./pages/Balances";
import Exceptions from "./pages/Exceptions";
import Reports from "./pages/Reports";
import Command from "./pages/Command";
import Dashboard from "./pages/Dashboard";
import TallyReport from "./pages/TallyReport";
import TallySheets from "./pages/TallySheets";
import LiveView from "./pages/LiveView";
import Login from "./pages/Login";
import Sessions from "./pages/Sessions";
import Webhooks from "./pages/Webhooks";
import Billing from "./pages/Billing";
import Console from "./pages/Console";
import ConsoleTenant from "./pages/ConsoleTenant";
import Onboard from "./pages/Onboard";
import PartnerInvoices from "./pages/PartnerInvoices";
import SupportAccess from "./pages/SupportAccess";
import Account from "./pages/Account";
import Certificates from "./pages/Certificates";
import SupportConsole from "./pages/SupportConsole";

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
  useEffect(() => {
    // the owner ended it, or its time ran out: back to support's own console
    const onEnded = () => switchBreakGlass(null, "/console/support");
    window.addEventListener("ivaas:break-glass-ended", onEnded);
    return () => window.removeEventListener("ivaas:break-glass-ended", onEnded);
  }, []);

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
    <ScopeProvider enabled={!!me.data?.tenant}>
      <LiveActivityProvider>
      <Layout connected={connected} me={me.data} onLogout={() => logout(config.data)}>
        {me.data?.tenant?.status === "cancelled" ? (
          // §3.3: a cancelled tenant keeps its export and nothing else
          <Routes>
            <Route path="/account" element={<Account me={me.data} />} />
            <Route path="*" element={<Navigate to="/account" replace />} />
          </Routes>
        ) : (
        <Routes>
          {/* platform and partner staff belong to no tenant: their home is the console */}
          <Route path="/" element={me.data && !me.data.tenant ? (
                <Navigate to={can(me.data, "tenant.create") ? "/console" : "/console/support"} replace />
              ) : <Dashboard me={me.data} />} />
          <Route path="/console" element={<Console me={me.data} />} />
          <Route path="/console/onboard" element={<Onboard me={me.data} />} />
          <Route path="/console/tenants/:id" element={<ConsoleTenant me={me.data} />} />
          <Route path="/console/partners" element={<PartnerInvoices me={me.data} />} />
          <Route path="/console/support" element={<SupportConsole />} />
          <Route path="/console/certificates" element={<Certificates />} />
          <Route path="/account" element={<Account me={me.data} />} />
          <Route path="/support-access" element={<SupportAccess />} />
          <Route path="/command" element={<Command />} />
          <Route path="/alerts" element={<Alerts me={me.data} />} />
          <Route path="/security" element={<Security me={me.data} />} />
          <Route path="/live" element={<LiveView />} />
          <Route path="/sessions" element={<Sessions me={me.data} />} />
          <Route path="/tally" element={<TallySheets />} />
          <Route path="/accuracy" element={<TallyReport />} />
          <Route path="/cameras" element={<Cameras me={me.data} />} />
          <Route path="/edge" element={<EdgeNodes me={me.data} />} />
          <Route path="/fleet" element={<Fleet me={me.data} />} />
          <Route path="/balances" element={<Balances />} />
          <Route path="/exceptions" element={<Exceptions me={me.data} />} />
          <Route path="/reports" element={<Reports />} />
          <Route path="/webhooks" element={<Webhooks />} />
          <Route path="/billing" element={<Billing me={me.data} />} />
          <Route path="/audit" element={<Audit />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/users" element={<Users me={me.data?.subject ?? ""} />} />
          <Route path="/account/password" element={<AccountPassword />} />
          <Route path="/analysis" element={<Analysis me={me.data} />} />
          <Route path="/analysis/:id" element={<AnalysisReport />} />
          <Route path="/assistant" element={<Assistant />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        )}
      </Layout>
      </LiveActivityProvider>
    </ScopeProvider>
  );
}
