import { useState, useEffect } from "react";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Save, Loader2, Check, AlertCircle, AlertTriangle, X } from "lucide-react";
import { useNetworkInterfaces } from "@/hooks/useNetworkInterfaces";
import { Checkbox } from "@/components/ui/checkbox";
import { postJson } from "@/lib/api";

// ── Confirm dialog (shown before saving) ─────────────────────────────────────

interface ConfirmDialogProps { onConfirm: () => void; onCancel: () => void; }
const ConfirmDialog = ({ onConfirm, onCancel }: ConfirmDialogProps) => (
  <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
    <div className="bg-card border border-border rounded-xl shadow-2xl w-full max-w-sm mx-4">
      <div className="flex items-center justify-between px-5 py-4 border-b border-border">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-full bg-warning/10 flex items-center justify-center">
            <AlertTriangle className="w-4 h-4 text-warning" />
          </div>
          <h2 className="text-sm font-semibold text-card-foreground">Save Configuration</h2>
        </div>
        <button onClick={onCancel} className="p-1 rounded hover:bg-accent transition-colors text-muted-foreground">
          <X className="w-4 h-4" />
        </button>
      </div>
      <div className="p-5">
        <p className="text-sm text-card-foreground">
          This will immediately restart the <span className="font-mono">voipd</span> service.
        </p>
        <p className="text-xs text-muted-foreground mt-2">VoIP calls in progress will be briefly interrupted.</p>
      </div>
      <div className="px-5 pb-4 flex justify-end gap-2">
        <button onClick={onCancel}
          className="px-4 py-2 rounded-md text-sm font-medium bg-secondary text-secondary-foreground hover:bg-accent transition-colors">
          Cancel
        </button>
        <button onClick={onConfirm}
          className="px-4 py-2 rounded-md text-sm font-medium bg-primary text-primary-foreground hover:bg-primary/90 transition-colors flex items-center gap-2">
          <Save className="w-3.5 h-3.5" /> Save & Restart
        </button>
      </div>
    </div>
  </div>
);

interface Config {
  VOIP_WAN_INTERFACE?: string; VOIP_WAN_VLAN?: string; VOIP_WAN_VLAN_INTERFACE?: string;
  VOIP_WAN_VLAN_INTERFACE_EGRESS_QOS?: string; PCSCF_HOSTNAME?: string; ROUTING_MODE?: string;
  VOIP_RT_TABLE?: string; VOIP_RT_TABLE_NAME?: string; VOIP_FORWARD_INTERFACE?: string;
  VOIP_FORWARD_GATEWAY?: string; VOIP_IMS_SUBNET?: string; VOIP_VPN_INTERFACES?: string; VOIP_DEBUG?: string;
}

const cosOptions = [
  { value: "0", label: "0 — Best Effort" }, { value: "1", label: "1 — Background" },
  { value: "2", label: "2 — Spare" },       { value: "3", label: "3 — Excellent Effort" },
  { value: "4", label: "4 — Controlled Load" }, { value: "5", label: "5 — Video (Voice)" },
  { value: "6", label: "6 — Voice (Network Control)" }, { value: "7", label: "7 — Network Control" },
];

const ConfigPanel = () => {
  const { wanInterfaces, lanInterfaces, vpnInterfaces, loading: ifaceLoading } = useNetworkInterfaces();
  const [config, setConfig] = useState<Config | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [saveResult, setSaveResult] = useState<{ ok: boolean; msg: string } | null>(null);
  const [forwardInterfaces, setForwardInterfaces] = useState<string[]>([]);
  const [vpnSelectedInterfaces, setVpnSelectedInterfaces] = useState<string[]>([]);

  useEffect(() => {
    fetch("/voip/api/config")
      .then(r => r.json())
      .then((data: Config) => {
        setConfig(data);
        setForwardInterfaces(
          data.VOIP_FORWARD_INTERFACE
            ? data.VOIP_FORWARD_INTERFACE.split(" ").filter(Boolean)
            : []
        );
        setVpnSelectedInterfaces(
          data.VOIP_VPN_INTERFACES
            ? data.VOIP_VPN_INTERFACES.split(" ").filter(Boolean)
            : []
        );
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  const handleChange = (key: keyof Config, value: string) =>
    setConfig(prev => prev ? { ...prev, [key]: value } : prev);

  const toggleForwardInterface = (name: string) => {
    setForwardInterfaces(prev => {
      const next = prev.includes(name) ? prev.filter(i => i !== name) : [...prev, name];
      setConfig(c => c ? { ...c, VOIP_FORWARD_INTERFACE: next.join(" ") } : c);
      return next;
    });
  };

  const toggleVpnInterface = (name: string) => {
    setVpnSelectedInterfaces(prev => {
      const next = prev.includes(name) ? prev.filter(i => i !== name) : [...prev, name];
      setConfig(c => c ? { ...c, VOIP_VPN_INTERFACES: next.join(" ") } : c);
      return next;
    });
  };

  const doSave = async () => {
    if (!config) return;
    setSaving(true);
    setShowConfirm(false);
    setSaveResult(null);
    try {
      const res = await postJson("/voip/api/config", config);
      const data = await res.json();
      if (!data.ok) {
        setSaveResult({ ok: false, msg: `Error: ${data.output}` });
        return;
      }
      // Poll until voipd is back up (max ~15s)
      setSaveResult({ ok: true, msg: "Configuration saved. Service restarting…" });
      let attempts = 0;
      const poll = setInterval(async () => {
        attempts++;
        try {
          const s = await fetch("/voip/api/status").then(r => r.json());
          if (s.serviceRunning) {
            clearInterval(poll);
            setSaveResult({ ok: true, msg: "Configuration saved. Service restarted." });
          } else if (attempts >= 15) {
            clearInterval(poll);
            setSaveResult({ ok: false, msg: "Service did not restart within 15s — check logs." });
          }
        } catch { /* keep polling */ }
      }, 1000);
    } catch (e) {
      setSaveResult({ ok: false, msg: `Network error: ${e}` });
    } finally {
      setSaving(false);
    }
  };

  if (loading) return (
    <div className="flex items-center justify-center h-40 text-muted-foreground gap-2">
      <Loader2 className="w-5 h-5 animate-spin" /> Loading configuration…
    </div>
  );

  if (!config) return (
    <div className="unifi-card">
      <div className="unifi-card-body text-sm text-destructive">
        Failed to load configuration. Make sure voip-ui service is running.
      </div>
    </div>
  );

  const egressCos = (config.VOIP_WAN_VLAN_INTERFACE_EGRESS_QOS?.match(/0:(\d)/)?.[1]) ?? "5";

  return (
    <>
      {showConfirm && <ConfirmDialog onConfirm={doSave} onCancel={() => setShowConfirm(false)} />}

      <div className="space-y-5">
        <div className="unifi-card">
          <div className="unifi-card-header">
            <h2 className="text-sm font-semibold text-card-foreground">VoIP Configuration</h2>
            <p className="text-xs text-muted-foreground mt-0.5">
              Changes are saved to voipd.conf and the service is restarted automatically
            </p>
          </div>
          <div className="unifi-card-body space-y-5">

            {/* WAN Interface */}
            <FieldGroup label="WAN Interface" hint="Physical WAN port detected on this device">
              {ifaceLoading ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground py-2">
                  <Loader2 className="w-4 h-4 animate-spin" /> Detecting interfaces…
                </div>
              ) : (
                <Select value={config.VOIP_WAN_INTERFACE ?? ""}
                  onValueChange={v => handleChange("VOIP_WAN_INTERFACE", v)}>
                  <SelectTrigger className="unifi-input">
                    <SelectValue placeholder="Select WAN interface" />
                  </SelectTrigger>
                  <SelectContent>
                    {wanInterfaces.map(iface => (
                      <SelectItem key={iface.name} value={iface.name}>
                        <span className="flex items-center gap-2">
                          <span className={`inline-block w-2 h-2 rounded-full ${
                            iface.status === "up" ? "bg-green-500" : "bg-muted-foreground/40"
                          }`} />
                          <span className="font-mono">{iface.name}</span>
                          <span className="text-muted-foreground">— {iface.description}</span>
                        </span>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </FieldGroup>

            {/* VLAN ID & QoS */}
            <FieldGroup label="VLAN ID & QoS"
              hint="VLAN tag your ISP uses for VoIP traffic and egress CoS marking">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-muted-foreground mb-1">VLAN ID</label>
                  <input type="text" value={config.VOIP_WAN_VLAN ?? ""}
                    onChange={e => handleChange("VOIP_WAN_VLAN", e.target.value)}
                    className="unifi-input" />
                </div>
                <div>
                  <label className="block text-xs font-medium text-muted-foreground mb-1">
                    Egress QoS (CoS)
                  </label>
                  <Select value={egressCos}
                    onValueChange={v => handleChange(
                      "VOIP_WAN_VLAN_INTERFACE_EGRESS_QOS",
                      Array.from({ length: 8 }, (_, i) => `${i}:${v}`).join(" ")
                    )}>
                    <SelectTrigger className="unifi-input"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {cosOptions.map(opt => (
                        <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <p className="text-xs text-muted-foreground mt-1.5">
                Egress QoS: <span className="font-mono">
                  {Array.from({ length: 8 }, (_, i) => `${i}:${egressCos}`).join(" ")}
                </span>
              </p>
            </FieldGroup>

            {/* P-CSCF Hostname */}
            <FieldGroup label="P-CSCF Hostname"
              hint="ISP's SIP proxy hostname for resolution at startup">
              <input type="text" value={config.PCSCF_HOSTNAME ?? ""}
                onChange={e => handleChange("PCSCF_HOSTNAME", e.target.value)}
                className="unifi-input" />
            </FieldGroup>

            {/* Routing Mode */}
            <FieldGroup label="Routing Mode"
              hint="PBR creates a dedicated routing table; Forward routes via an existing interface">
              <div className="flex gap-2">
                {["pbr", "forward"].map(mode => (
                  <button key={mode} onClick={() => handleChange("ROUTING_MODE", mode)}
                    className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
                      config.ROUTING_MODE === mode
                        ? "bg-primary text-primary-foreground"
                        : "bg-secondary text-secondary-foreground hover:bg-accent"
                    }`}>
                    {mode.toUpperCase()}
                  </button>
                ))}
              </div>
            </FieldGroup>

            {/* Forward Interfaces — only in forward mode */}
            {config.ROUTING_MODE === "forward" && (
              <FieldGroup label="Forward Interfaces"
                hint="Select one or more VLAN interfaces for VoIP traffic routing">
                {ifaceLoading ? (
                  <div className="flex items-center gap-2 text-sm text-muted-foreground py-2">
                    <Loader2 className="w-4 h-4 animate-spin" /> Detecting interfaces…
                  </div>
                ) : lanInterfaces.length === 0 ? (
                  <p className="text-sm text-muted-foreground py-2">No VLAN interfaces detected</p>
                ) : (
                  <div className="space-y-2 rounded-md border border-border p-3">
                    {lanInterfaces.map(iface => {
                      const checked = forwardInterfaces.includes(iface.name);
                      return (
                        <label key={iface.name}
                          className={`flex items-center gap-3 rounded-md px-3 py-2 cursor-pointer transition-colors ${
                            checked ? "bg-accent/50" : "hover:bg-accent/30"
                          }`}>
                          <Checkbox checked={checked}
                            onCheckedChange={() => toggleForwardInterface(iface.name)} />
                          <span className={`inline-block w-2 h-2 rounded-full ${
                            iface.status === "up" ? "bg-green-500" : "bg-muted-foreground/40"
                          }`} />
                          <span className="font-mono text-sm">{iface.name}</span>
                          <span className="text-xs text-muted-foreground">{iface.description}</span>
                        </label>
                      );
                    })}
                    {forwardInterfaces.length > 0 && (
                      <p className="text-xs text-muted-foreground pt-1 border-t border-border mt-1">
                        Selected: <span className="font-mono">{forwardInterfaces.join(" ")}</span>
                      </p>
                    )}
                  </div>
                )}
              </FieldGroup>
            )}

            {/* Routing Table */}
            <FieldGroup label="PBR Routing Table Number"
              hint="Auto-detected at install. Only change if you have a table conflict.">
              <input type="text" value={config.VOIP_RT_TABLE ?? ""}
                onChange={e => handleChange("VOIP_RT_TABLE", e.target.value)}
                className="unifi-input w-32" />
            </FieldGroup>

            {/* IMS Subnet Override */}
            <FieldGroup label="IMS Subnet Override"
              hint="Leave empty for auto-detection. Override format: x.x.x.x/y">
              <input type="text" value={config.VOIP_IMS_SUBNET ?? ""}
                onChange={e => handleChange("VOIP_IMS_SUBNET", e.target.value)}
                className="unifi-input" placeholder="Auto-detect" />
            </FieldGroup>

            {/* VPN Support */}
            <FieldGroup label="VPN Support"
              hint="Allow SIP/RTP over WireGuard VPN (Teleport or built-in VPN server). Opt-in — disabled by default.">
              {ifaceLoading ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground py-2">
                  <Loader2 className="w-4 h-4 animate-spin" /> Detecting interfaces…
                </div>
              ) : vpnInterfaces.length === 0 ? (
                <p className="text-sm text-muted-foreground py-2">No WireGuard interfaces detected</p>
              ) : (
                <div className="space-y-2 rounded-md border border-border p-3">
                  {vpnInterfaces.map(iface => {
                    const checked = vpnSelectedInterfaces.includes(iface.name);
                    return (
                      <label key={iface.name}
                        className={`flex items-center gap-3 rounded-md px-3 py-2 cursor-pointer transition-colors ${
                          checked ? "bg-accent/50" : "hover:bg-accent/30"
                        }`}>
                        <Checkbox checked={checked}
                          onCheckedChange={() => toggleVpnInterface(iface.name)} />
                        <span className={`inline-block w-2 h-2 rounded-full shrink-0 ${
                          iface.status === "up" ? "bg-green-500" : "bg-muted-foreground/40"
                        }`} />
                        <span className="text-sm text-card-foreground">{iface.description}</span>
                        <span className="text-xs text-muted-foreground font-mono">{iface.name}</span>
                        <span className="text-xs text-muted-foreground/60">WireGuard VPN</span>
                      </label>
                    );
                  })}
                  {vpnSelectedInterfaces.length > 0 && (
                    <p className="text-xs text-muted-foreground pt-1 border-t border-border mt-1">
                      Enabled: <span className="font-mono">{vpnSelectedInterfaces.join(" ")}</span>
                    </p>
                  )}
                </div>
              )}
            </FieldGroup>
          </div>

          {/* Footer: inline result + save button */}
          <div className="px-5 py-4 border-t border-border flex items-center justify-between">
            {saveResult ? (
              <span className={`flex items-center gap-1.5 text-xs ${
                saveResult.ok ? "text-success" : "text-destructive"
              }`}>
                {saveResult.ok
                  ? <Check className="w-3 h-3" />
                  : <AlertCircle className="w-3 h-3" />}
                {saveResult.msg}
              </span>
            ) : <span />}
            <button onClick={() => setShowConfirm(true)} disabled={saving}
              className="flex items-center gap-2 px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50">
              {saving
                ? <Loader2 className="w-4 h-4 animate-spin" />
                : <Save className="w-4 h-4" />}
              Save & Restart
            </button>
          </div>
        </div>

        {/* IMS Subnet Detection Reference */}
        <div className="unifi-card">
          <div className="unifi-card-header">
            <h3 className="text-sm font-semibold text-card-foreground">IMS Subnet Auto-Detection</h3>
          </div>
          <div className="unifi-card-body">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border">
                  <th className="text-left py-2 text-xs font-medium text-muted-foreground uppercase tracking-wide">
                    First Octet
                  </th>
                  <th className="text-left py-2 text-xs font-medium text-muted-foreground uppercase tracking-wide">
                    Subnet Used
                  </th>
                </tr>
              </thead>
              <tbody className="text-card-foreground">
                {[
                  ["10.x.x.x",  "10.0.0.0/8"],
                  ["100.x.x.x", "100.64.0.0/10"],
                  ["172.x.x.x", "172.16.0.0/12"],
                  ["192.x.x.x", "192.168.0.0/16"],
                  ["Other",     "10.0.0.0/8 (fallback)"],
                ].map(([octet, subnet]) => (
                  <tr key={octet} className="border-b border-border last:border-0">
                    <td className="py-2 font-mono text-xs">{octet}</td>
                    <td className="py-2 font-mono text-xs">{subnet}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </>
  );
};

const FieldGroup = ({
  label, hint, children,
}: {
  label: string; hint: string; children: React.ReactNode;
}) => (
  <div>
    <label className="block text-sm font-medium text-card-foreground mb-1">{label}</label>
    <p className="text-xs text-muted-foreground mb-2">{hint}</p>
    {children}
  </div>
);

export default ConfigPanel;
