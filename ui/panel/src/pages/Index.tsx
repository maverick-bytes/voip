import { useState } from "react";
import { ChevronRight } from "lucide-react";
import AppSidebar from "@/components/AppSidebar";
import ThemeToggle from "@/components/ThemeToggle";
import StatusPanel from "@/components/StatusPanel";
import ConfigPanel from "@/components/ConfigPanel";
import LogsPanel from "@/components/LogsPanel";
import CommandsPanel from "@/components/CommandsPanel";
import HelpPanel from "@/components/HelpPanel";

const titles: Record<string, string> = {
  status: "Service Status",
  config: "Configuration",
  logs: "Logs",
  commands: "Commands",
  help: "Help & FAQ",
};

const Index = () => {
  const [activeTab, setActiveTab] = useState("status");

  const renderPanel = () => {
    switch (activeTab) {
      case "status": return <StatusPanel />;
      case "config": return <ConfigPanel />;
      case "logs": return <LogsPanel />;
      case "commands": return <CommandsPanel />;
      case "help": return <HelpPanel />;
      default: return <StatusPanel />;
    }
  };

  // Return to UniFi OS. If this tab arrived here from another console page
  // (e.g. the user typed /voip while on Settings), go back to that exact
  // page; otherwise land on the console root.
  //
  // navigation.entries() lists only SAME-ORIGIN session history entries —
  // cross-origin entries are excluded entirely, so an external site can
  // never become the back-target. The console's documents are served with a
  // strict Referrer-Policy, which per spec hides their URLs from later
  // documents (entry.url === null) — but a listed entry is same-origin by
  // definition, so a hidden-URL previous entry IS a console page and is safe
  // to traverse to. Entries with visible /voip URLs (our own pages) are
  // skipped. Browsers without the API (Firefox/Safari) get the console root.
  const returnToUnifi = () => {
    try {
      const nav = (window as unknown as { navigation?: any }).navigation;
      if (nav?.entries && nav.currentEntry) {
        const entries = nav.entries();
        for (let i = (nav.currentEntry.index ?? 0) - 1; i >= 0; i--) {
          const e = entries[i];
          if (!e) break;
          if (e.url) {
            const url = new URL(e.url);
            if (url.origin !== window.location.origin) break;
            if (url.pathname.startsWith("/voip")) continue; // our own pages
          }
          // Visible non-/voip same-origin URL, or same-origin entry with the
          // URL hidden by the console's referrer policy → a console page.
          nav.traverseTo(e.key);
          return;
        }
      }
    } catch {
      /* fall through to the console root */
    }
    window.location.href = "/";
  };

  return (
    <div className="flex min-h-screen bg-background">
      <AppSidebar activeTab={activeTab} onTabChange={setActiveTab} />
      <main className="flex-1 flex flex-col min-h-screen min-w-0">
        <header className="h-14 border-b border-border flex items-center justify-between px-5 bg-card">
          <div className="flex items-center gap-2 min-w-0 overflow-hidden">
            <button
              type="button"
              onClick={returnToUnifi}
              title="Return to the UniFi OS console"
              className="inline-flex items-center gap-2 pl-1.5 pr-3 py-[5px] rounded-[9px] bg-primary/[0.09] hover:bg-primary/[0.18] transition-colors whitespace-nowrap"
            >
              <span className="w-6 h-6 rounded-[7px] bg-primary/15 flex items-center justify-center text-primary flex-none">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="3" width="7" height="7" rx="1.6" />
                  <rect x="14" y="3" width="7" height="7" rx="1.6" />
                  <rect x="14" y="14" width="7" height="7" rx="1.6" />
                  <rect x="3" y="14" width="7" height="7" rx="1.6" />
                </svg>
              </span>
              <span className="text-[12.5px] font-semibold text-primary leading-none relative top-[0.38px]">UniFi OS</span>
            </button>
            <ChevronRight className="w-3.5 h-3.5 text-muted-foreground opacity-55 flex-none" />
            <span className="text-[13.5px] font-semibold text-card-foreground truncate min-w-0">
              {titles[activeTab]}
            </span>
          </div>
          <ThemeToggle />
        </header>
        <div className="flex-1 p-6 overflow-auto">
          {renderPanel()}
        </div>
      </main>
    </div>
  );
};

export default Index;
