import { useState, useEffect } from "react";

export interface NetworkInterface {
  name: string;
  type: "wan" | "lan" | "virtual";
  description: string;
  status: "up" | "down";
}

export function useNetworkInterfaces() {
  const [wanInterfaces, setWanInterfaces] = useState<NetworkInterface[]>([]);
  const [lanInterfaces, setLanInterfaces] = useState<NetworkInterface[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchInterfaces = async () => {
      setLoading(true);
      try {
        const res = await fetch("/voip/api/interfaces");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        setWanInterfaces(data.wanInterfaces ?? []);
        setLanInterfaces(data.lanInterfaces ?? []);
      } catch (err) {
        console.error("Failed to fetch network interfaces:", err);
        // Fallback to empty — UI shows "no interfaces detected"
        setWanInterfaces([]);
        setLanInterfaces([]);
      } finally {
        setLoading(false);
      }
    };
    fetchInterfaces();
  }, []);

  return { wanInterfaces, lanInterfaces, loading };
}
