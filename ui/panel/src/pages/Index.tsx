import { useState } from "react";
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

  return (
    <div className="flex min-h-screen bg-background">
      <AppSidebar activeTab={activeTab} onTabChange={setActiveTab} />
      <main className="flex-1 flex flex-col min-h-screen">
        <header className="h-14 border-b border-border flex items-center justify-between px-6 bg-card">
          <h2 className="text-sm font-semibold text-card-foreground">{titles[activeTab]}</h2>
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
