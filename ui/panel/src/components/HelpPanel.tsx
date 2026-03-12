import { ExternalLink } from "lucide-react";

const faqs = [
  {
    q: "What VLAN ID should I use?",
    a: "Check your ISP's ONT web UI WAN page or use OMCI commands via telnet from your ONT unit. The default in this script is VLAN 11.",
  },
  {
    q: "Does this support IPv6?",
    a: "Currently only IPv4 is configured. IPv6 support can be added if needed.",
  },
  {
    q: "Will this survive a reboot?",
    a: "Yes, the systemd service is enabled to start automatically on boot. However, after a firmware upgrade you need to reinstall the service.",
  },
  {
    q: "VoIP interface has no IP",
    a: "The VLAN ID or WAN interface name may be wrong for your device model. Check with: ip addr show voip",
  },
  {
    q: "SIP proxy not resolved",
    a: "Verify the DNS server from the DHCP lease file at /run/systemd/netif/leases/<index>. Test manually with nslookup.",
  },
  {
    q: "Calls fail or one-way audio",
    a: "Do NOT load nf_nat_sip or nf_conntrack_sip modules. They corrupt SRTP key material. Run: lsmod | grep sip (expect no output).",
  },
];

const HelpPanel = () => {
  return (
    <div className="space-y-5">
      <div className="unifi-card">
        <div className="unifi-card-header flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold text-card-foreground">FAQ & Troubleshooting</h2>
            <p className="text-xs text-muted-foreground mt-0.5">Common questions and solutions</p>
          </div>
          <a
            href="https://github.com/maverick-bytes/voip"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1.5 text-xs text-primary hover:underline font-medium"
          >
            GitHub <ExternalLink className="w-3 h-3" />
          </a>
        </div>
        <div className="divide-y divide-border">
          {faqs.map((faq, i) => (
            <div key={i} className="px-5 py-4">
              <h3 className="text-sm font-medium text-card-foreground">{faq.q}</h3>
              <p className="text-xs text-muted-foreground mt-1.5 leading-relaxed">{faq.a}</p>
            </div>
          ))}
        </div>
      </div>

      {/* SIP Client Config Reference */}
      <div className="unifi-card">
        <div className="unifi-card-header">
          <h3 className="text-sm font-semibold text-card-foreground">SIP Client Configuration</h3>
        </div>
        <div className="unifi-card-body space-y-3">
          {[
            ["SIP Server / Registrar", "Your ISP's SIP domain"],
            ["Outbound Proxy", "The SIP Proxy IP shown in Status"],
            ["SIP Username / Password", "Your ISP's provided credentials"],
            ["Transport", "UDP / TCP / TLS"],
          ].map(([label, value]) => (
            <div key={label} className="flex items-center justify-between">
              <span className="text-xs font-medium text-muted-foreground">{label}</span>
              <span className="text-xs font-mono text-card-foreground">{value}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

export default HelpPanel;
