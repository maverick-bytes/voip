import { useState, useEffect } from "react";
import { Terminal, Download, Trash2, RefreshCw, CheckCircle, Loader2, AlertTriangle, X, Check } from "lucide-react";
import { postJson } from "@/lib/api";

// ── Shared result dialog (shown after any command completes) ──────────────────

interface ResultDialogProps {
  ok: boolean;
  output: string;
  title: string;
  onClose: () => void;
}

const ResultDialog = ({ ok, output, title, onClose }: ResultDialogProps) => (
  <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
    <div className="bg-card border border-border rounded-xl shadow-2xl w-full max-w-lg mx-4">
      <div className="flex items-center justify-between px-5 py-4 border-b border-border">
        <div className="flex items-center gap-2.5">
          <div className={`w-8 h-8 rounded-full flex items-center justify-center ${ok ? "bg-success/10" : "bg-destructive/10"}`}>
            {ok ? <Check className="w-4 h-4 text-success" /> : <AlertTriangle className="w-4 h-4 text-destructive" />}
          </div>
          <h2 className="text-sm font-semibold text-card-foreground">
            {title} — {ok ? "Completed" : "Failed"}
          </h2>
        </div>
        <button onClick={onClose} className="p-1 rounded hover:bg-accent transition-colors text-muted-foreground">
          <X className="w-4 h-4" />
        </button>
      </div>
      <div className="p-5">
        <pre className={`text-xs font-mono whitespace-pre-wrap break-all max-h-64 overflow-auto p-3 rounded-lg ${
          ok ? "bg-success/5 text-success border border-success/20" : "bg-destructive/5 text-destructive border border-destructive/20"
        }`}>
          {output || (ok ? "Done." : "Command returned an error.")}
        </pre>
      </div>
      <div className="px-5 pb-4 flex justify-end">
        <button onClick={onClose}
          className="px-4 py-2 rounded-md text-sm font-medium bg-primary text-primary-foreground hover:bg-primary/90 transition-colors">
          OK
        </button>
      </div>
    </div>
  </div>
);

// ── Uninstall scope dialog (shown before running uninstall) ───────────────────

interface UninstallDialogProps {
  onConfirm: (scope: "daemon" | "ui" | "all") => void;
  onCancel: () => void;
}

const UninstallDialog = ({ onConfirm, onCancel }: UninstallDialogProps) => (
  <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
    <div className="bg-card border border-border rounded-xl shadow-2xl w-full max-w-md mx-4">
      <div className="flex items-center justify-between px-5 py-4 border-b border-border">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-full bg-destructive/10 flex items-center justify-center">
            <AlertTriangle className="w-4 h-4 text-destructive" />
          </div>
          <h2 className="text-sm font-semibold text-card-foreground">Uninstall — Choose Scope</h2>
        </div>
        <button onClick={onCancel} className="p-1 rounded hover:bg-accent transition-colors text-muted-foreground">
          <X className="w-4 h-4" />
        </button>
      </div>
      <div className="p-5 space-y-3">
        <p className="text-xs text-muted-foreground mb-4">
          Select what to remove. Your <span className="font-mono text-card-foreground">voipd.conf</span> is preserved unless you choose "Everything".
        </p>
        <button onClick={() => onConfirm("daemon")}
          className="w-full text-left p-4 rounded-lg border border-border hover:border-warning/50 hover:bg-warning/5 transition-colors group">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-card-foreground">Daemon only</span>
            <span className="text-xs text-muted-foreground group-hover:text-warning transition-colors font-mono">./voip uninstall</span>
          </div>
          <p className="text-xs text-muted-foreground mt-1">Stops voipd, removes systemd service and routing rules. Config and web UI are kept.</p>
        </button>
        <button onClick={() => onConfirm("ui")}
          className="w-full text-left p-4 rounded-lg border border-border hover:border-warning/50 hover:bg-warning/5 transition-colors group">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-card-foreground">Web UI only</span>
            <span className="text-xs text-muted-foreground group-hover:text-warning transition-colors font-mono">./voip uninstall-ui</span>
          </div>
          <p className="text-xs text-muted-foreground mt-1">Removes the web UI service and nginx config. voipd daemon keeps running.</p>
        </button>
        <button onClick={() => onConfirm("all")}
          className="w-full text-left p-4 rounded-lg border border-destructive/30 hover:border-destructive hover:bg-destructive/5 transition-colors group">
          <div className="flex items-center justify-between">
            <span className="text-sm font-semibold text-destructive">Everything</span>
            <span className="text-xs text-muted-foreground group-hover:text-destructive transition-colors">full removal</span>
          </div>
          <p className="text-xs text-muted-foreground mt-1">
            Removes daemon, web UI, nginx config, and <span className="font-mono">/data/voip</span>. Cannot be undone.
          </p>
        </button>
      </div>
      <div className="px-5 pb-4 flex justify-end">
        <button onClick={onCancel}
          className="px-4 py-2 rounded-md text-sm font-medium bg-secondary text-secondary-foreground hover:bg-accent transition-colors">
          Cancel
        </button>
      </div>
    </div>
  </div>
);

// ── Command definitions ───────────────────────────────────────────────────────

const commands = [
  {
    id: "install",
    label: "Install / Reinstall Service",
    description: "Install voipd and enable it as a systemd service. Run this again after a firmware upgrade — systemd service files don't survive firmware updates.",
    command: "cd /data/voip && ./voip install",
    icon: Download,
    variant: "install" as const,
  },
  {
    id: "update",
    label: "Update Service & UI",
    description: "Pull latest scripts + UI bundle from GitHub. voipd.conf is preserved.",
    command: "cd /data/voip && ./voip update",
    icon: RefreshCw,
    variant: "update" as const,
  },
  {
    id: "verify",
    label: "Verify Routing",
    description: "Show routing table and policy rules to confirm VoIP traffic path",
    command: "ip route show table <voip> && ip rule show | grep '100:'",
    icon: CheckCircle,
    variant: "verify" as const,
  },

  {
    id: "uninstall",
    label: "Uninstall",
    description: "Remove the VoIP service, web UI, or everything. You choose.",
    command: "./voip uninstall / uninstall-ui / full removal",
    icon: Trash2,
    variant: "destructive" as const,
    hasDialog: true,
  },
];

const variantStyles = {
  install:     "bg-primary text-primary-foreground hover:bg-primary/90",
  warning:     "bg-warning/15 text-warning hover:bg-warning/25",
  destructive: "bg-destructive/10 text-destructive hover:bg-destructive/20",
  update:      "bg-success/15 text-success hover:bg-success/25",
  verify:      "bg-accent text-accent-foreground hover:bg-secondary",
};

// ── Main panel ────────────────────────────────────────────────────────────────

const CommandsPanel = () => {
  const [running, setRunning] = useState<string | null>(null);
  const [showUninstallDialog, setShowUninstallDialog] = useState(false);
  const [result, setResult] = useState<{ label: string; ok: boolean; output: string } | null>(null);

  const runCommand = async (cmdId: string, label: string) => {
    setRunning(cmdId);
    try {
      const res = await postJson("/voip/api/command", { command: cmdId });
      const data = await res.json();
      setResult({ label, ok: data.ok, output: data.output ?? "" });
    } catch (e) {
      setResult({ label, ok: false, output: `Network error: ${e}` });
    } finally {
      setRunning(null);
    }
  };

  const handleUninstallConfirm = (scope: "daemon" | "ui" | "all") => {
    setShowUninstallDialog(false);
    const map = { daemon: ["uninstall", "Uninstall Daemon"], ui: ["uninstall-ui", "Uninstall Web UI"], all: ["uninstall-all", "Full Removal"] } as const;
    runCommand(map[scope][0], map[scope][1]);
  };

  return (
    <>
      {showUninstallDialog && (
        <UninstallDialog onConfirm={handleUninstallConfirm} onCancel={() => setShowUninstallDialog(false)} />
      )}
      {result && (
        <ResultDialog ok={result.ok} output={result.output} title={result.label} onClose={() => setResult(null)} />
      )}

      <div className="space-y-4">
        {commands.map(cmd => {
          const Icon = cmd.icon;
          const isRunning = running === cmd.id
            || (cmd.id === "uninstall" && ["uninstall", "uninstall-ui", "uninstall-all"].includes(running ?? ""));
          return (
            <div key={cmd.id} className="unifi-card">
              <div className="unifi-card-body flex items-start justify-between gap-4">
                <div className="flex items-start gap-3 flex-1 min-w-0">
                  <div className="w-9 h-9 rounded-lg bg-muted flex items-center justify-center shrink-0 mt-0.5">
                    <Icon className="w-4 h-4 text-muted-foreground" />
                  </div>
                  <div className="min-w-0">
                    <h3 className="text-sm font-semibold text-card-foreground">{cmd.label}</h3>
                    <p className="text-xs text-muted-foreground mt-0.5">{cmd.description}</p>
                    <code className="inline-block mt-2 px-2 py-1 rounded bg-muted text-xs font-mono text-muted-foreground">
                      {cmd.command}
                    </code>
                  </div>
                </div>
                <button
                  onClick={() => cmd.hasDialog ? setShowUninstallDialog(true) : runCommand(cmd.id, cmd.label)}
                  disabled={!!running}
                  className={`shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors disabled:opacity-50 ${variantStyles[cmd.variant]}`}
                >
                  {isRunning ? <Loader2 className="w-3 h-3 animate-spin" /> : <Terminal className="w-3 h-3" />}
                  {isRunning ? "Running…" : "Run"}
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
};

export default CommandsPanel;
