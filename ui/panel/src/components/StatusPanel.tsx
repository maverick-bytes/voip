import { useEffect, useState, useCallback } from "react";
import { Activity, Wifi, Globe, Router, Shield, Phone, Loader2 } from "lucide-react";
import { postJson } from "@/lib/api";

interface StatusData {
  serviceRunning: boolean; interface: string; vlan: string; wanInterface: string;
  voipIp: string; gateway: string; routingMode: string; routingTable: string;
  imsSubnet: string; natRule: string; sipProxy: string; uptime: string;
  b2buaRegistered?: string | null; b2buaClients?: string | null; b2buaListenPort?: string;
}

const StatusPanel = () => {
  const [status, setStatus] = useState<StatusData | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionPending, setActionPending] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<string | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch("/voip/api/status");
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

  const runCommand = async (cmd: string, label: string) => {
    setActionPending(cmd);
    setActionMsg(null);
    try {
      const res = await postJson("/voip/api/command", { command: cmd });
      const data = await res.json();
      setActionMsg(data.ok ? `${label} succeeded.` : `Error: ${data.output}`);
      setTimeout(fetchStatus, 2000);
    } catch (e) {
      setActionMsg(`Network error: ${e}`);
    } finally {
      setActionPending(null);
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
  return (
    <div className="space-y-5">
      {/* Service Status Banner */}
      <div className="unifi-card">
        <div className="unifi-card-body flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className={`w-10 h-10 rounded-full flex items-center justify-center ${
              s.serviceRunning ? "bg-success/10" : "bg-destructive/10"
            }`}>
              <Activity className={`w-5 h-5 ${s.serviceRunning ? "text-success" : "text-destructive"}`} />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className={`status-dot ${s.serviceRunning ? "status-dot-online" : "status-dot-offline"}`} />
                <span className="text-sm font-semibold text-card-foreground">
                  {s.serviceRunning ? "Service Running" : "Service Stopped"}
                </span>
              </div>
              <p className="text-xs text-muted-foreground mt-0.5">
                voipd.service{s.uptime ? ` • Uptime: ${s.uptime}` : ""}
              </p>
            </div>
          </div>
          <div className="flex gap-2">
            {s.serviceRunning ? (
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
        {actionMsg && (
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
        <InfoCard icon={<Shield className="w-4 h-4" />} label="Routing"
          value={`${s.routingMode} → table ${s.routingTable}`} />
        <InfoCard icon={<Globe className="w-4 h-4" />} label="IMS Subnet" value={s.imsSubnet || "—"} />
        <InfoCard icon={<Shield className="w-4 h-4" />} label="NAT"        value={s.natRule} small />
      </div>

      {/* SIP Proxy / B2BUA endpoint */}
      <div className="unifi-card border-primary/30 bg-primary/5">
        <div className="unifi-card-body flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-primary/10 flex items-center justify-center">
            <Phone className="w-5 h-5 text-primary" />
          </div>
          <div>
            {s.routingMode === "B2BUA" ? (
              <>
                <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">SIP Registrar (B2BUA)</p>
                <p className="text-lg font-mono font-semibold text-card-foreground">
                  {s.sipProxy || "Resolving…"}
                </p>
                <p className="text-xs text-muted-foreground">Register your SIP client to this gateway address</p>
              </>
            ) : (
              <>
                <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">SIP Proxy</p>
                <p className="text-lg font-mono font-semibold text-card-foreground">
                  {s.sipProxy && s.sipProxy !== "(unresolved)" ? s.sipProxy : "Not resolved yet"}
                </p>
                <p className="text-xs text-muted-foreground">Use this address in your SIP client / ATA</p>
              </>
            )}
          </div>
        </div>
      </div>

      {/* B2BUA upstream status — only shown in b2bua mode */}
      {s.routingMode === "B2BUA" && (
        <div className="unifi-card">
          <div className="unifi-card-header">
            <h3 className="text-sm font-semibold text-card-foreground">B2BUA Status</h3>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 unifi-card-body">
            <InfoCard icon={<Shield className="w-4 h-4" />} label="Upstream Registration"
              value={
                s.b2buaRegistered === "True"  ? "✓ Registered" :
                s.b2buaRegistered === "False" ? "✗ Not registered" :
                "Starting…"
              } />
            <InfoCard icon={<Phone className="w-4 h-4" />} label="Local Clients"
              value={s.b2buaClients || "None registered"} />
          </div>
        </div>
      )}
    </div>
  );
};

const InfoCard = ({ icon, label, value, small }: {
  icon: React.ReactNode; label: string; value: string; small?: boolean;
}) => (
  <div className="unifi-card">
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
