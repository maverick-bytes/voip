import { useEffect, useState, useCallback, CSSProperties } from "react";
import { Loader2 } from "lucide-react";
import { postJson, apiFetch } from "@/lib/api";

interface StatusData {
  serviceRunning: boolean; interface: string; vlan: string; wanInterface: string;
  voipIp: string; gateway: string; routingMode: string; routingTable: string;
  imsSubnet: string; natRule: string; sipProxy: string; uptime: string;
  b2buaRegistered?: string | null; b2buaClients?: string | null; b2buaListenPort?: string;
  cpuPercent?: number; memoryMb?: number; procCount?: number;
}

// Values match the Claude Design mockup exactly; sourced from the app's CSS
// variables so both light and dark themes work.
const C = {
  card: "hsl(var(--card))", cardFg: "hsl(var(--card-foreground))",
  mutedFg: "hsl(var(--muted-foreground))", border: "hsl(var(--border))",
  primary: "hsl(var(--primary))", success: "hsl(var(--success))",
  warning: "hsl(var(--warning))", danger: "hsl(var(--destructive))",
  bg: "hsl(var(--background))",
};
const MONO = "ui-monospace,Menlo,Monaco,Consolas,'Liberation Mono',monospace";

const isB2bua = (m: string) => m === "b2bua" || m === "b2bua_netns";
const isErrorMsg = (m: string) => m.startsWith("Error") || m.startsWith("Network error");

const StatusPanel = () => {
  const [status, setStatus] = useState<StatusData | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionPending, setActionPending] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  const [transition, setTransition] = useState<"restarting" | "stopping" | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await apiFetch("/voip/api/status");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setStatus(await res.json());
    } catch (e) {
      console.error("Failed to fetch status:", e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    const id = setInterval(fetchStatus, 8000);
    return () => clearInterval(id);
  }, [fetchStatus]);

  // Auto-dismiss success notices ~5s after they become visible (success text
  // only renders once the restart/stop transition has settled). Error notices
  // stay until the next action so there is time to read them.
  useEffect(() => {
    if (!actionMsg || isErrorMsg(actionMsg) || transition) return;
    const t = setTimeout(() => setActionMsg(null), 5000);
    return () => clearTimeout(t);
  }, [actionMsg, transition]);

  const pollUntil = useCallback(async (wantRunning: boolean, attempts = 12, intervalMs = 1500) => {
    for (let i = 0; i < attempts; i++) {
      await new Promise((r) => setTimeout(r, intervalMs));
      try {
        const res = await apiFetch("/voip/api/status");
        if (res.ok) {
          const data: StatusData = await res.json();
          setStatus(data);
          if (data.serviceRunning === wantRunning) return true;
        }
      } catch { /* keep trying */ }
    }
    return false;
  }, []);

  const runCommand = async (cmd: string, label: string) => {
    setActionPending(cmd);
    setActionMsg(null);
    if (cmd === "restart" || cmd === "start") setTransition("restarting");
    else if (cmd === "stop") setTransition("stopping");
    try {
      const res = await postJson("/voip/api/command", { command: cmd });
      const data = await res.json();
      setActionMsg(data.ok ? `${label} succeeded.` : `Error: ${data.output}`);
      if (data.ok) await pollUntil(cmd !== "stop");
      else await fetchStatus();
    } catch (e) {
      setActionMsg(`Network error: ${e}`);
      await fetchStatus();
    } finally {
      setActionPending(null);
      setTransition(null);
    }
  };

  if (loading) return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: 160, gap: 8, color: C.mutedFg }}>
      <Loader2 className="w-5 h-5 animate-spin" /> Loading status…
    </div>
  );
  if (!status) return (
    <div style={cardStyle()}>
      <div style={{ padding: "16px 20px", fontSize: 13, color: C.danger }}>
        Failed to reach the VoIP API. Make sure the voip-ui service is running.
      </div>
    </div>
  );

  const s = status;
  const running = s.serviceRunning;
  const statusColor = transition ? C.primary : running ? C.success : C.danger;
  const statusVar = transition ? "primary" : running ? "success" : "destructive";
  const statusText = transition === "restarting" ? "Restarting…"
    : transition === "stopping" ? "Stopping…"
    : running ? "Service Running" : "Service Stopped";

  const routingDisplay =
    s.routingMode === "b2bua_netns" ? `B2BUA NETNS → Table ${s.routingTable}`
    : s.routingMode === "b2bua"     ? `B2BUA → Table ${s.routingTable}`
    : s.routingMode === "pbr"       ? `PBR → Table ${s.routingTable}`
    : "FORWARD";

  const showMetrics = running && !transition;
  const cpu = s.cpuPercent ?? 0;
  const mem = s.memoryMb ?? 0;
  const cpuColor = cpu >= 80 ? C.danger : cpu >= 50 ? C.warning : C.success;
  // Segment bars: 5 segments — one step per 20% CPU, one per 40 MB memory
  // (200 MB full scale, so the typical ~35 MB footprint reads as a single
  // segment). Any non-zero value lights at least the first segment so
  // light-but-real activity (e.g. an active call at <1% CPU) is visible.
  const cpuSegs = cpu <= 0 ? 0 : Math.min(5, Math.max(1, Math.round(cpu / 20)));
  const memSegs = mem <= 0 ? 0 : Math.min(5, Math.max(1, Math.round(mem / 40)));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>

      {/* ===== Service status banner ===== */}
      <div className="dc-banner" style={{ ...cardStyle(11), padding: "18px 22px", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 20, flexWrap: "wrap" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 14, minWidth: 0 }}>
          <div style={{ width: 44, height: 44, borderRadius: 12, background: `hsl(var(--${statusVar}) / 0.13)`, display: "flex", alignItems: "center", justifyContent: "center", color: statusColor, flex: "none" }}>
            {transition
              ? <Loader2 className="w-5 h-5 animate-spin" />
              : <svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12" /></svg>}
          </div>
          <div style={{ minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ width: 9, height: 9, borderRadius: "50%", background: statusColor, boxShadow: `0 0 6px hsl(var(--${statusVar}) / 0.55)`, animation: transition ? "none" : "dotpulse 2.4s ease-in-out infinite", flex: "none" }} />
              <span style={{ font: "600 15px 'Inter',sans-serif", color: C.cardFg }}>{statusText}</span>
            </div>
            <p style={{ margin: "3px 0 0", font: "400 13px 'Inter',sans-serif", color: C.mutedFg }}>
              voipd.service{!transition && s.uptime ? `\u00a0\u00a0\u00b7\u00a0\u00a0Uptime: ${s.uptime}` : ""}
            </p>
          </div>
        </div>

        <div className="dc-banner-right">
          {showMetrics && (
            <>
              <div className="dc-banner-metrics">
                <BannerMetric label="CPU" value={cpu.toFixed(1)} unit="%" lit={cpuSegs} litColor={cpuColor}
                  icon={<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="4" y="4" width="16" height="16" rx="2" /><rect x="9" y="9" width="6" height="6" /><path d="M9 2v2M15 2v2M9 20v2M15 20v2M2 9h2M2 15h2M20 9h2M20 15h2" /></svg>} />
                <BannerMetric label="MEMORY" value={mem.toFixed(1)} unit="MB" lit={memSegs} litColor={C.success}
                  icon={<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="8" width="18" height="9" rx="1.5" /><path d="M7 12v3M11 12v3M15 12v3M19 12v3" /></svg>} />
              </div>
              <span className="dc-banner-divider" aria-hidden="true" />
            </>
          )}
          <div className="dc-banner-actions">
            {transition ? (
              <>
                <BtnStatic label="Stop" varName="destructive" />
                <BtnStatic label="Restart" varName="primary" />
              </>
            ) : running ? (
              <>
                <Btn label="Stop" varName="destructive" pending={actionPending === "stop"} disabled={!!actionPending} onClick={() => runCommand("stop", "Stop")} />
                <Btn label="Restart" varName="primary" pending={actionPending === "restart"} disabled={!!actionPending} onClick={() => runCommand("restart", "Restart")} />
              </>
            ) : (
              <Btn label="Start" varName="success" pending={actionPending === "start"} disabled={!!actionPending} onClick={() => runCommand("start", "Start")} />
            )}
          </div>
        </div>
      </div>
      {actionMsg && (transition === null || isErrorMsg(actionMsg)) && (
        <div style={{ marginTop: -12, padding: "0 4px", fontSize: 12, color: isErrorMsg(actionMsg) ? C.danger : C.success }}>
          {actionMsg}
        </div>
      )}

      {/* ===== Info grid ===== */}
      <div className="dc-g3">
        <InfoCard label="Interface" value={`${s.interface || "voip"} (VLAN ${s.vlan} on ${s.wanInterface})`}
          icon={<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 20h.01" /><path d="M2 8.82a15 15 0 0 1 20 0" /><path d="M5 12.859a10 10 0 0 1 14 0" /><path d="M8.5 16.429a5 5 0 0 1 7 0" /></svg>} />
        <InfoCard label="VoIP IP" value={s.voipIp || "—"}
          icon={<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10" /><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20" /><path d="M2 12h20" /></svg>} />
        <InfoCard label="Gateway" value={s.gateway || "—"}
          icon={<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect width="20" height="8" x="2" y="14" rx="2" /><path d="M6.01 18H6M10.01 18H10M15 10v4" /><path d="M17.84 7.17a4 4 0 0 0-5.66 0M20.66 4.34a8 8 0 0 0-11.31 0" /></svg>} />
        <InfoCard label="Routing" value={routingDisplay}
          icon={<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /></svg>} />
        <InfoCard label="IMS Subnet" value={s.imsSubnet || "—"}
          icon={<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10" /><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20" /><path d="M2 12h20" /></svg>} />
        <InfoCard label="NAT" value={s.natRule} smaller
          icon={<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /></svg>} />
      </div>

      {/* ===== SIP registrar / proxy highlight card ===== */}
      <div style={{ background: "hsl(var(--primary) / 0.05)", border: "1px solid hsl(var(--primary) / 0.3)", borderRadius: 11, padding: "18px 22px", display: "flex", alignItems: "center", gap: 15 }}>
        <div style={{ width: 44, height: 44, borderRadius: "50%", background: "hsl(var(--primary) / 0.12)", display: "flex", alignItems: "center", justifyContent: "center", color: C.primary, flex: "none" }}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z" /></svg>
        </div>
        <div style={{ minWidth: 0 }}>
          <p style={{ margin: 0, font: "600 12px 'Inter',sans-serif", letterSpacing: ".05em", textTransform: "uppercase", color: C.mutedFg }}>
            {isB2bua(s.routingMode) ? `SIP Registrar — ${s.routingMode === "b2bua_netns" ? "B2BUA NETNS" : "B2BUA"}` : "SIP Proxy (P-CSCF)"}
          </p>
          <p style={{ margin: "4px 0 3px", font: `600 19px ${MONO}`, color: C.cardFg, wordBreak: "break-all" }}>
            {s.sipProxy && s.sipProxy !== "(unresolved)" ? s.sipProxy : "Not resolved yet"}
          </p>
          <p style={{ margin: 0, font: "400 13px 'Inter',sans-serif", color: C.mutedFg }}>
            {isB2bua(s.routingMode)
              ? "Register your SIP client to this address — the sandboxed B2BUA handles upstream IMS registration"
              : "Configure this address in your SIP client or ATA"}
          </p>
        </div>
      </div>

      {/* ===== B2BUA NETNS status card ===== */}
      {isB2bua(s.routingMode) && (
        <div style={{ ...cardStyle(11), overflow: "hidden" }}>
          <div style={{ padding: "15px 20px", borderBottom: `1px solid ${C.border}` }}>
            <h3 style={{ margin: 0, font: "600 14px 'Inter',sans-serif", color: C.cardFg }}>
              {s.routingMode === "b2bua_netns" ? "B2BUA NETNS Status" : "B2BUA Status"}
            </h3>
          </div>
          <div className="dc-g2" style={{ padding: "18px 20px" }}>
            <NestedCard label="Upstream Registration"
              icon={<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /></svg>}
              value={
                s.b2buaRegistered === "True"
                  ? <><span style={{ color: C.success }}>✓</span> Registered to IMS</>
                  : s.b2buaRegistered === "False"
                  ? <><span style={{ color: C.danger }}>✗</span> Not registered</>
                  : "Starting…"
              } />
            <NestedCard label="Local Clients"
              icon={<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z" /></svg>}
              value={s.b2buaClients || "None registered"} />
            {s.routingMode === "b2bua_netns" && (
              <NestedCard fullWidth smaller label="Sandbox"
                icon={<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /></svg>}
                value="Active — traffic via veth/netns through FORWARD chain" />
            )}
          </div>
        </div>
      )}
    </div>
  );
};

// ---- helpers ---------------------------------------------------------------

const cardStyle = (radius = 11): CSSProperties => ({
  background: C.card, border: `1px solid ${C.border}`,
  borderRadius: radius, boxShadow: "0 1px 3px rgba(0,0,0,.14)",
});

const labelStyle: CSSProperties = {
  font: "600 12px 'Inter',sans-serif", letterSpacing: ".05em", textTransform: "uppercase",
};

const BannerMetric = ({ icon, label, value, unit, lit, litColor }: {
  icon: React.ReactNode; label: string; value: string; unit: string; lit: number; litColor: string;
}) => (
  <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
    <span style={{ display: "flex", alignItems: "center", gap: 5, font: "600 11px/1 'Inter',sans-serif", letterSpacing: ".08em", color: C.mutedFg }}>
      {icon}{label}
    </span>
    <span style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
      <span style={{ font: `600 26px/1 ${MONO}`, color: C.cardFg, letterSpacing: "-.02em" }}>{value}</span>
      <span style={{ font: `500 13px/1 ${MONO}`, color: C.mutedFg }}>{unit}</span>
    </span>
    <span style={{ display: "flex", gap: 3 }}>
      {Array.from({ length: 5 }).map((_, i) => (
        <i key={i} style={{ width: 15, height: 4, borderRadius: 2, background: i < lit ? litColor : C.border }} />
      ))}
    </span>
  </div>
);

const Btn = ({ label, varName, pending, disabled, onClick }: {
  label: string; varName: "destructive" | "primary" | "success"; pending: boolean; disabled: boolean; onClick: () => void;
}) => (
  <button type="button" onClick={onClick} disabled={disabled}
    style={{
      padding: "8px 14px", borderRadius: 8, border: "none",
      background: `hsl(var(--${varName}) / 0.14)`, color: `hsl(var(--${varName}))`,
      font: "500 13px 'Inter',sans-serif", cursor: disabled ? "default" : "pointer",
      opacity: disabled && !pending ? 0.5 : 1, minWidth: 58,
    }}>
    {pending ? <Loader2 className="w-3 h-3 animate-spin inline" /> : label}
  </button>
);

const BtnStatic = ({ label, varName }: { label: string; varName: string }) => (
  <button type="button" disabled
    style={{ padding: "8px 14px", borderRadius: 8, border: "none", background: `hsl(var(--${varName}) / 0.14)`, color: `hsl(var(--${varName}))`, font: "500 13px 'Inter',sans-serif", cursor: "default", opacity: 0.4, minWidth: 58 }}>
    {label}
  </button>
);

const InfoCard = ({ icon, label, value, smaller }: {
  icon: React.ReactNode; label: string; value: string; smaller?: boolean;
}) => (
  <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 11, padding: "16px 20px" }}>
    <div style={{ display: "flex", alignItems: "center", gap: 7, color: C.mutedFg, marginBottom: 10 }}>
      {icon}<span style={labelStyle}>{label}</span>
    </div>
    <p style={{ margin: 0, font: `500 ${smaller ? 13 : 14}px ${MONO}`, color: C.cardFg, wordBreak: "break-word" }}>{value}</p>
  </div>
);

const NestedCard = ({ icon, label, value, smaller, fullWidth }: {
  icon: React.ReactNode; label: string; value: React.ReactNode; smaller?: boolean; fullWidth?: boolean;
}) => (
  <div style={{ gridColumn: fullWidth ? "1 / -1" : undefined, background: C.bg, border: `1px solid ${C.border}`, borderRadius: 10, padding: "15px 18px" }}>
    <div style={{ display: "flex", alignItems: "center", gap: 7, color: C.mutedFg, marginBottom: 9 }}>
      {icon}<span style={labelStyle}>{label}</span>
    </div>
    <p style={{ margin: 0, display: "flex", alignItems: "center", gap: 6, font: `500 ${smaller ? 13 : 14}px ${MONO}`, color: C.cardFg, wordBreak: "break-word" }}>{value}</p>
  </div>
);

export default StatusPanel;
