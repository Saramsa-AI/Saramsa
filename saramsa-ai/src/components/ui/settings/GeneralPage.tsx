"use client";

import { useEffect, useState } from "react";
import { Mail } from "lucide-react";
import { apiRequest } from "@/lib/apiRequest";

type Profile = {
  email?: string;
  first_name?: string;
  last_name?: string;
};

export function GeneralPage() {
  const [profile, setProfile] = useState<Profile>({});
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const me = await apiRequest("get", "/auth/me/", undefined, true);
        const payload = me.data?.data || me.data || {};
        setProfile({
          email: payload.email || "",
          first_name: payload.first_name || "",
          last_name: payload.last_name || "",
        });
      } catch {
        setProfile({});
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const fullName = `${profile.first_name || ""} ${profile.last_name || ""}`.trim();

  return (
    <div className="space-y-8">
      <header>
        <h2 className="text-lg font-semibold text-foreground">General</h2>
        <p className="text-sm text-muted-foreground mt-2">Your account details.</p>
      </header>

      <section className="rounded-lg border border-border bg-card">
        <div className="border-b border-border px-6 py-4">
          <h3 className="text-sm font-medium text-foreground">Account</h3>
        </div>
        <div className="px-6 py-5">
          <dl className="grid grid-cols-1 sm:grid-cols-[140px_1fr] gap-x-6 gap-y-5 text-sm">
            <dt className="text-muted-foreground">Email</dt>
            <dd className="flex items-center gap-2 text-foreground">
              <Mail className="h-4 w-4 text-muted-foreground" />
              <span className="font-medium">{loading ? "…" : profile.email || "—"}</span>
            </dd>
            {fullName && (
              <>
                <dt className="text-muted-foreground">Name</dt>
                <dd className="text-foreground font-medium">{fullName}</dd>
              </>
            )}
          </dl>
        </div>
      </section>
    </div>
  );
}
