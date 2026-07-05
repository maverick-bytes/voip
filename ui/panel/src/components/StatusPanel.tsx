import { useEffect, useState, useCallback } from "react";
import { Activity, Wifi, Globe, Router, Shield, Phone, Loader2 } from "lucide-react";
import { postJson, apiFetch } from "@/lib/api";

interface StatusData {
  serviceRunning: boolean; interface: string; vlan: string; wanInterface: string;
  voipIp: string; gateway: string; routingMode: string; routingTable: string;
  imsSubnet: string; natRule: string; sipProxy: string; uptime: string;
  b2buaRegistered?: string | null; b2buaClients?: string | null; b2buaListenPort?: string;
}

const ROUTING_LABELS: Record<string, string> = {
  b2bua_netns: "B2BUA NETNS",
  b2bua:       "B2BUA",
  pbr:         "PBR",
  forward:     "FORWARD",
};

const isB2bua = (mode: string) => mode === "b2bua" || mode === "b2bua_netns";

const StatusPanel = () => {
  const [status, setStatus] = useState<StatusData | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionPending, setActionPending] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  // While a restart/start is settling, voipd briefly reports inactive. We
  // suppress the red "Service Stopped" flash during this window and show a
  // transitional "Restarting…" state instead, until the service comes back.
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

  // Poll the status endpoint repeatedly until it reports the expected running
  // state (or we run out of attempts). Used after a service-control action so
  // the badge reflects reality quickly instead of waiting for the 8s poll,
  // and without flashing a stale "Stopped" while the service is still coming up.
  const pollUntil = useCallback(async (
    wantRunning: boolean,
    attempts = 12,
    intervalMs = 1500,
  ) => {
    for (let i = 0; i < attempts; i++) {
      await new Promise((r) => setTimeout(r, intervalMs));
      try {
        const res = await apiFetch("/voip/api/status");
        if (res.ok) {
          const data: StatusData = await res.json();
          setStatus(data);
          if (data.serviceRunning === wantRunning) return true;
        }
      } catch {
        /* keep trying */
      }
    }
    return false;
  }, []);

  const runCommand = async (cmd: string, label: string) => {
    setActionPending(cmd);
    setActionMsg(null);
    // Set the transitional state up-front so the banner never flashes red
    // "Stopped" mid-restart.
    if (cmd === "restart" || cmd === "start") setTransition("restarting");
    else if (cmd === "stop") setTransition("stopping");
    try {
      const res = await postJson("/voip/api/command", { command: cmd });
      const data = await res.json();
      setActionMsg(data.ok ? `${label} succeeded.` : `Error: ${data.output}`);
      if (data.ok) {
        // Wait for the service to actually reach the expected state before
        // clearing the transitional banner.
        const wantRunning = cmd !== "stop";
        await pollUntil(wantRunning);
      } else {
        await fetchStatus();
      }
    } catch (e) {
      setActionMsg(`Network error: ${e}`);
      await fetchStatus();
    } finally {
      setActionPending(null);
      setTransition(null);
    }
  };

  if (loading) return (
    <div className="flex items-center justify-center h-40 text-muted-foreground gap-2">
      <Loader2 className="w-5 h-5 animate-spin" /> Loading status…
    </div>
  );

  if (!status) return (
    <div className="unifi-card">
      <div className="unifi-card-body text-sm text-destructive">
        Failed to reach the VoIP API. Make sure voip-ui service is running.
      </div>
    </div>
  );

  const s = status;
  const modeLabel = ROUTING_LABELS[s.routingMode] ?? s.routingMode.toUpperCase();
  const routingDisplay = s.routingMode === "b2bua_netns"
    ? `B2BUA NETNS → Table ${s.routingTable}`
    : s.routingMode === "b2bua"
    ? `B2BUA → Table ${s.routingTable}`
    : s.routingMode === "pbr"
    ? `PBR → Table ${s.routingTable}`
    : `FORWARD`;

  return (
    <div className="space-y-5">
      {/* Service Status Banner */}
      <div className="unifi-card">
        <div className="unifi-card-body flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className={`w-10 h-10 rounded-full flex items-center justify-center ${
              transition ? "bg-primary/10"
                         : s.serviceRunning ? "bg-success/10" : "bg-destructive/10"
            }`}>
              {transition ? (
                <Loader2 className="w-5 h-5 text-primary animate-spin" />
              ) : (
                <Activity className={`w-5 h-5 ${s.serviceRunning ? "text-success" : "text-destructive"}`} />
              )}
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className={`status-dot ${
                  transition ? "status-dot-warning"
                             : s.serviceRunning ? "status-dot-online" : "status-dot-offline"
                }`} />
                <span className="text-sm font-semibold text-card-foreground">
                  {transition === "restarting" ? "Restarting…"
                    : transition === "stopping" ? "Stopping…"
                    : s.serviceRunning ? "Service Running" : "Service Stopped"}
                </span>
              </div>
              <p className="text-xs text-muted-foreground mt-0.5">
                voipd.service{!transition && s.uptime ? ` • Uptime: ${s.uptime}` : ""}
              </p>
            </div>
          </div>
          <div className="flex gap-2">
            {/* During a transition (restart/start/stop settling) keep the
                Stop | Restart buttons in place but disabled and dimmed — the
                stable layout plus the darkened, non-clickable controls read
                clearly as "busy", and the animated ring in the status banner
                already supplies the motion (so no redundant spinner in here).
                We deliberately show Stop | Restart rather than the raw
                serviceRunning branch, which would flip to a contradictory
                "Start" button mid-restart. */}
            {transition ? (
              <>
                <button disabled
                  className="px-3 py-1.5 text-xs font-medium rounded-md bg-destructive/10 text-destructive opacity-40 cursor-default">
                  Stop
                </button>
                <button disabled
                  className="px-3 py-1.5 text-xs font-medium rounded-md bg-primary/10 text-primary opacity-40 cursor-default">
                  Restart
                </button>
              </>
            ) : s.serviceRunning ? (
              <>
                <button onClick={() => runCommand("stop", "Stop")}
                  disabled={!!actionPending}
                  className="px-3 py-1.5 text-xs font-medium rounded-md bg-destructive/10 text-destructive hover:bg-destructive/20 transition-colors disabled:opacity-50">
                  {actionPending === "stop" ? <Loader2 className="w-3 h-3 animate-spin inline" /> : "Stop"}
                </button>
                <button onClick={() => runCommand("restart", "Restart")}
                  disabled={!!actionPending}
                  className="px-3 py-1.5 text-xs font-medium rounded-md bg-primary/10 text-primary hover:bg-primary/20 transition-colors disabled:opacity-50">
                  {actionPending === "restart" ? <Loader2 className="w-3 h-3 animate-spin inline" /> : "Restart"}
                </button>
              </>
            ) : (
              <button onClick={() => runCommand("start", "Start")}
                disabled={!!actionPending}
                className="px-3 py-1.5 text-xs font-medium rounded-md bg-success/10 text-success hover:bg-success/20 transition-colors disabled:opacity-50">
                {actionPending === "start" ? <Loader2 className="w-3 h-3 animate-spin inline" /> : "Start"}
              </button>
            )}
          </div>
        </div>
        {/* Suppress the success message while still transitioning — showing
            "Restart succeeded" under a "Restarting…" banner reads as
            contradictory. Errors are always shown immediately. */}
        {actionMsg && (transition === null || actionMsg.startsWith("Error")) && (
          <div className={`px-5 pb-3 text-xs ${actionMsg.startsWith("Error") ? "text-destructive" : "text-success"}`}>
            {actionMsg}
          </div>
        )}
      </div>

      {/* Info Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        <InfoCard icon={<Wifi className="w-4 h-4" />} label="Interface"
          value={`${s.interface || "voip"} (VLAN ${s.vlan} on ${s.wanInterface})`} />
        <InfoCard icon={<Globe className="w-4 h-4" />} label="VoIP IP"    value={s.voipIp || "—"} />
        <InfoCard icon={<Router className="w-4 h-4" />} label="Gateway"   value={s.gateway || "—"} />
        <InfoCard icon={<Shield className="w-4 h-4" />} label="Routing"   value={routingDisplay} />
        <InfoCard icon={<Globe className="w-4 h-4" />} label="IMS Subnet" value={s.imsSubnet || "—"} />
        <InfoCard icon={<Shield className="w-4 h-4" />} label="NAT"       value={s.natRule} small />
      </div>

      {/* SIP endpoint card — B2BUA / B2BUA NETNS */}
      {isB2bua(s.routingMode) && (
        <div className="unifi-card border-primary/30 bg-primary/5">
          <div className="unifi-card-body flex items-center gap-3">
            <div className="w-10 h-10 rounded-full bg-primary/10 flex items-center justify-center">
              <Phone className="w-5 h-5 text-primary" />
            </div>
            <div>
              <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">
                SIP Registrar — {s.routingMode === "b2bua_netns" ? "B2BUA NETNS" : "B2BUA"}
              </p>
              <p className="text-lg font-mono font-semibold text-card-foreground">
                {s.sipProxy || "Resolving…"}
              </p>
              <p className="text-xs text-muted-foreground">
                {s.routingMode === "b2bua_netns"
                  ? "Register your SIP client to this address — the sandboxed B2BUA handles upstream IMS registration"
                  : "Register your SIP client to this gateway address"}
              </p>
            </div>
          </div>
        </div>
      )}

      {/* SIP endpoint card — PBR / Forward */}
      {!isB2bua(s.routingMode) && (
        <div className="unifi-card border-primary/30 bg-primary/5">
          <div className="unifi-card-body flex items-center gap-3">
            <div className="w-10 h-10 rounded-full bg-primary/10 flex items-center justify-center">
              <Phone className="w-5 h-5 text-primary" />
            </div>
            <div>
              <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">SIP Proxy (P-CSCF)</p>
              <p className="text-lg font-mono font-semibold text-card-foreground">
                {s.sipProxy && s.sipProxy !== "(unresolved)" ? s.sipProxy : "Not resolved yet"}
              </p>
              <p className="text-xs text-muted-foreground">Configure this address in your SIP client or ATA</p>
            </div>
          </div>
        </div>
      )}

      {/* B2BUA status block — shown for b2bua and b2bua_netns */}
      {isB2bua(s.routingMode) && (
        <div className="unifi-card">
          <div className="unifi-card-header">
            <h3 className="text-sm font-semibold text-card-foreground">
              {s.routingMode === "b2bua_netns" ? "B2BUA NETNS Status" : "B2BUA Status"}
            </h3>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 unifi-card-body">
            <InfoCard icon={<Shield className="w-4 h-4" />} label="Upstream Registration"
              value={
                s.b2buaRegistered === "True"  ? "✓ Registered to IMS" :
                s.b2buaRegistered === "False" ? "✗ Not registered" :
                "Starting…"
              } />
            <InfoCard icon={<Phone className="w-4 h-4" />} label="Local Clients"
              value={s.b2buaClients || "None registered"} />
            {s.routingMode === "b2bua_netns" && (
              <InfoCard icon={<Shield className="w-4 h-4" />} label="Sandbox"
                value="Active — traffic via veth/netns through FORWARD chain"
                small className="md:col-span-2" />
            )}
          </div>
        </div>
      )}
    </div>
  );
};

const InfoCard = ({ icon, label, value, small, className }: {
  icon: React.ReactNode; label: string; value: string; small?: boolean; className?: string;
}) => (
  <div className={`unifi-card${className ? ` ${className}` : ""}`}>
    <div className="unifi-card-body">
      <div className="flex items-center gap-2 text-muted-foreground mb-1.5">
        {icon}
        <span className="text-xs font-medium uppercase tracking-wide">{label}</span>
      </div>
      <p className={`font-mono font-medium text-card-foreground ${small ? "text-xs" : "text-sm"}`}>{value}</p>
    </div>
  </div>
);

export default StatusPanel;
