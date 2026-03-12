import { useEffect, useRef, useState, useCallback } from "react";
import { RefreshCw, Loader2, Bug, Check, AlertCircle } from "lucide-react";
import { postJson } from "@/lib/api";

// ── Inline toggle switch ──────────────────────────────────────────────────────

const ToggleSwitch = ({ checked, onChange, disabled }: {
  checked: boolean; onChange: () => void; disabled?: boolean;
}) => (
  <button
    role="switch"
    aria-checked={checked}
    onClick={onChange}
    disabled={disabled}
    className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent
      transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring
      disabled:opacity-50 disabled:cursor-not-allowed ${checked ? "bg-primary" : "bg-input"}`}
  >
    <span className={`pointer-events-none block h-4 w-4 rounded-full bg-white shadow-lg ring-0
      transition-transform duration-200 ${checked ? "translate-x-4" : "translate-x-0"}`} />
  </button>
);

// ── Types ─────────────────────────────────────────────────────────────────────

interface LogEntry { time: string; level: string; msg: string; }

const levelColors: Record<string, string> = {
  info:    "text-muted-foreground",
  success: "text-success",
  warning: "text-warning",
  error:   "text-destructive",
  debug:   "text-primary/70",
};

// ── LogsPanel ─────────────────────────────────────────────────────────────────

const LogsPanel = () => {
  const [logs, setLogs]           = useState<LogEntry[]>([]);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState<string | null>(null);
  // null = still loading initial config; boolean = known state
  const [debugMode, setDebugMode] = useState<boolean | null>(null);
  const [toggling, setToggling]   = useState(false);
  const [toggleMsg, setToggleMsg] = useState<{ ok: boolean; text: string; phase: "restarting" | "done" } | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  // ── Fetch logs ──────────────────────────────────────────────────────────────
  const fetchLogs = useCallback(async () => {
    try {
      const res = await fetch("/voip/api/logs");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setLogs(await res.json());
      setError(null);
    } catch (e) {
      setError(`Failed to fetch logs: ${e}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchLogs();
    const id = setInterval(fetchLogs, 5000);
    return () => clearInterval(id);
  }, [fetchLogs]);

  // ── Load current debug state from config on mount ───────────────────────────
  useEffect(() => {
    fetch("/voip/api/config")
      .then(r => r.json())
      .then(data => setDebugMode(data.VOIP_DEBUG === "true"))
      .catch(() => setDebugMode(false));
  }, []);

  // ── Auto-scroll to bottom whenever new log lines arrive ─────────────────────
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  // ── Toggle debug mode ────────────────────────────────────────────────────────
  // Reads the full config, flips VOIP_DEBUG, POSTs back. The config endpoint
  // writes voipd.conf and runs `systemctl restart voipd` before returning, so
  // the service is restarting before the API call resolves. We show an
  // intermediate "Restarting voipd…" message immediately, then settle once done.
  const handleDebugToggle = async () => {
    if (debugMode === null || toggling) return;
    const next = !debugMode;
    setToggling(true);
    setToggleMsg({ ok: true, text: "Restarting voipd…", phase: "restarting" });
    try {
      const cfgRes = await fetch("/voip/api/config");
      if (!cfgRes.ok) throw new Error("Failed to read config");
      const cfg = await cfgRes.json();
      cfg.VOIP_DEBUG = next ? "true" : "false";
      const saveRes = await postJson("/voip/api/config", cfg);
      if (!saveRes.ok) throw new Error(`HTTP ${saveRes.status}`);
      setDebugMode(next);
      setToggleMsg({
        ok: true,
        text: next
          ? "Debug mode enabled — verbose [DEBUG] lines will appear in logs."
          : "Debug mode disabled — service restarted.",
        phase: "done",
      });
    } catch (e) {
      setToggleMsg({ ok: false, text: `Failed to apply: ${e}`, phase: "done" });
    } finally {
      setToggling(false);
    }
  };


  return (
    <div className="unifi-card">
      {/* Header ── title left, debug toggle + refresh right */}
      <div className="unifi-card-header flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-card-foreground">Service Logs</h2>
          <p className="text-xs text-muted-foreground mt-0.5">
            journalctl -u voipd — auto-refreshes every 5s
          </p>
        </div>

        <div className="flex items-center gap-3">
          {/* Debug toggle */}
          <div className="flex items-center gap-2">
            {toggling
              ? <Loader2 className="w-3.5 h-3.5 animate-spin text-muted-foreground" />
              : <Bug className={`w-3.5 h-3.5 ${debugMode ? "text-primary" : "text-muted-foreground/40"}`} />
            }
            <span className="text-xs text-muted-foreground select-none">Debug</span>
            <ToggleSwitch
              checked={debugMode === true}
              disabled={debugMode === null || toggling}
              onChange={handleDebugToggle}
            />
          </div>

          {/* Divider */}
          <div className="w-px h-4 bg-border" />

          {/* Refresh */}
          <button onClick={fetchLogs}
            className="p-2 rounded-md hover:bg-accent transition-colors text-muted-foreground"
            title="Refresh now">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
          </button>
        </div>
      </div>

      {/* Inline debug toggle status ── appears below header, above log output */}
      {toggleMsg && (
        <div className={`px-5 py-2 text-xs border-b border-border flex items-center gap-1.5 ${
          toggleMsg.phase === "restarting"
            ? "text-muted-foreground bg-muted/30"
            : toggleMsg.ok
              ? "text-success bg-success/5"
              : "text-destructive bg-destructive/5"
        }`}>
          {toggleMsg.phase === "restarting"
            ? <Loader2 className="w-3 h-3 animate-spin shrink-0" />
            : toggleMsg.ok
              ? <Check className="w-3 h-3 shrink-0" />
              : <AlertCircle className="w-3 h-3 shrink-0" />
          }
          {toggleMsg.text}
        </div>
      )}

      {/* Log output */}
      <div className="bg-sidebar text-sidebar-foreground font-mono text-xs overflow-auto max-h-[calc(100vh-270px)]">
        {error ? (
          <div className="p-4 text-destructive">{error}</div>
        ) : logs.length === 0 && !loading ? (
          <div className="p-4 text-muted-foreground">No log entries found. Is voipd installed?</div>
        ) : (
          <div className="p-4 space-y-0.5">
            {logs.map((log, i) => (
              <div key={i} className="flex gap-3 leading-relaxed">
                <span className="text-sidebar-foreground/50 shrink-0 whitespace-nowrap">{log.time}</span>
                <span className={`shrink-0 w-16 ${levelColors[log.level] || "text-sidebar-foreground"}`}>
                  [{log.level}]
                </span>
                <span className="text-sidebar-accent-foreground break-all">{log.msg}</span>
              </div>
            ))}
            <div ref={bottomRef} />
          </div>
        )}
      </div>
    </div>
  );
};

export default LogsPanel;
