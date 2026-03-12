import { Phone, Settings, Activity, FileText, Terminal, HelpCircle, Heart } from "lucide-react";

interface AppSidebarProps {
  activeTab: string;
  onTabChange: (tab: string) => void;
}

const navItems = [
  { id: "status",   label: "Status",        icon: Activity   },
  { id: "config",   label: "Configuration", icon: Settings   },
  { id: "logs",     label: "Logs",          icon: FileText   },
  { id: "commands", label: "Commands",      icon: Terminal   },
  { id: "help",     label: "Help",          icon: HelpCircle },
];

const AppSidebar = ({ activeTab, onTabChange }: AppSidebarProps) => {
  return (
    <aside className="w-56 bg-sidebar border-r border-sidebar-border flex flex-col min-h-screen">
      <div className="px-4 py-5 border-b border-sidebar-border flex items-center gap-2.5">
        <div className="w-8 h-8 rounded-lg bg-primary flex items-center justify-center">
          <Phone className="w-4 h-4 text-primary-foreground" />
        </div>
        <div>
          <h1 className="text-sm font-semibold text-sidebar-accent-foreground">VoIP</h1>
          <p className="text-[10px] text-sidebar-foreground">UniFi OS</p>
        </div>
      </div>

      <nav className="flex-1 py-3 px-2 space-y-0.5">
        {navItems.map(item => {
          const Icon = item.icon;
          const active = activeTab === item.id;
          return (
            <button
              key={item.id}
              onClick={() => onTabChange(item.id)}
              className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-md text-sm transition-colors ${
                active
                  ? "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
                  : "text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
              }`}
            >
              <Icon className="w-4 h-4" />
              {item.label}
            </button>
          );
        })}
      </nav>

      <div className="px-4 py-3 border-t border-sidebar-border space-y-2">
        <a
          href="https://ko-fi.com/H2H31UPAFR"
          target="_blank"
          rel="noopener noreferrer"
          className="w-full flex items-center gap-2 px-3 py-2 rounded-md text-sm font-medium bg-[hsl(340,80%,55%)] text-white hover:bg-[hsl(340,80%,48%)] transition-colors"
        >
          <Heart className="w-4 h-4" />
          Donate
        </a>
        <p className="text-[10px] text-sidebar-foreground">v1.1.0 • GPL-2.0</p>
      </div>
    </aside>
  );
};

export default AppSidebar;
