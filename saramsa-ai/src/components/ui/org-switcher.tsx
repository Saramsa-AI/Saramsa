"use client";

import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Building2, Check, Loader2, Plus } from "lucide-react";
import { useAuth } from "@/lib/useAuth";
import { Button } from "@/components/ui/button";

export function OrgSwitcher() {
  const { user, switchOrganization } = useAuth();
  const [open, setOpen] = useState(false);
  const [switchingId, setSwitchingId] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    if (open) {
      document.addEventListener("mousedown", onClick);
      return () => document.removeEventListener("mousedown", onClick);
    }
  }, [open]);

  const orgs = user?.organizations ?? [];
  const activeId = user?.active_organization_id;
  const activeName =
    user?.active_organization?.name ||
    orgs.find((o) => o.id === activeId)?.name ||
    "No workspace";
  const contextError = user?.organization_context_error;

  if (!user) return null;

  // Backend failed to load workspace context — show an explicit warning chip
  // so the user knows the empty org list is "load failed", not "no memberships".
  if (orgs.length === 0 && contextError) {
    return (
      <button
        type="button"
        onClick={() => {
          if (typeof window !== "undefined") window.location.reload();
        }}
        title={`Workspace context unavailable: ${contextError}. Click to retry.`}
        className="h-9 px-3 inline-flex items-center gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 text-xs font-medium text-amber-300 hover:bg-amber-500/15"
      >
        <AlertTriangle className="h-3.5 w-3.5" />
        Workspace unavailable — retry
      </button>
    );
  }

  if (orgs.length === 0) return null;

  const handleSwitch = async (id: string) => {
    if (id === activeId) {
      setOpen(false);
      return;
    }
    setSwitchingId(id);
    const result = await switchOrganization(id);
    setSwitchingId(null);
    if (result.success) {
      setOpen(false);
      if (typeof window !== "undefined") window.location.reload();
    }
  };

  return (
    <div className="relative" ref={ref}>
      <Button
        type="button"
        variant="ghost"
        onClick={() => setOpen((v) => !v)}
        className="h-9 px-3 gap-2 text-sm font-medium text-foreground hover:bg-secondary/60"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <Building2 className="h-4 w-4 text-saramsa-brand" />
        <span className="max-w-[160px] truncate">{activeName}</span>
      </Button>
      {open && (
        <div
          role="menu"
          className="absolute z-50 right-0 mt-2 w-72 rounded-lg border border-border/60 bg-popover shadow-lg dark:bg-popover/95"
        >
          <div className="px-3 py-2 text-[11px] uppercase tracking-wide text-muted-foreground border-b border-border/60">
            Switch workspace
          </div>
          <div className="py-1 max-h-72 overflow-y-auto">
            {orgs.map((org) => {
              const isActive = org.id === activeId;
              const isSwitching = switchingId === org.id;
              return (
                <button
                  key={org.id}
                  role="menuitem"
                  onClick={() => handleSwitch(org.id)}
                  disabled={isSwitching}
                  className="flex items-center justify-between w-full px-3 py-2 text-sm text-foreground hover:bg-accent/60 disabled:opacity-60"
                >
                  <span className="flex items-center gap-2 min-w-0">
                    <Building2 className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    <span className="truncate">{org.name || org.id}</span>
                  </span>
                  {isSwitching ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                  ) : isActive ? (
                    <Check className="h-3.5 w-3.5 text-saramsa-brand" />
                  ) : null}
                </button>
              );
            })}
          </div>
          <div className="border-t border-border/60 py-1">
            <button
              role="menuitem"
              onClick={() => {
                setOpen(false);
                window.location.href = "/settings?tab=workspace";
              }}
              className="flex items-center gap-2 w-full px-3 py-2 text-sm text-foreground hover:bg-accent/60"
            >
              <Plus className="h-3.5 w-3.5" />
              Manage workspaces
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
