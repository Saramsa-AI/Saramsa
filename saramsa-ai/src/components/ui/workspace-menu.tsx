"use client";

import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Building2, Check, ChevronDown, Loader2, LogOut, Plus, Settings } from "lucide-react";
import { toast } from "sonner";
import { useAuth } from "@/lib/useAuth";
import { Button } from "@/components/ui/button";

/**
 * Single navbar account/workspace menu.
 *
 * Replaces the old pair of adjacent dropdowns — OrgSwitcher ("{name} Workspace")
 * and the first-letter avatar button — which split four related actions across
 * two triggers that looked unrelated. The trigger is now just the workspace
 * name; one click exposes all four:
 *
 *   1. switch workspace   2. manage workspaces   3. settings   4. logout
 *
 * Note this must render even when the user belongs to no organization: the old
 * OrgSwitcher returned null in that case, which is harmless when a separate
 * avatar still carries Settings/Logout, but would strip them entirely here.
 */
export function WorkspaceMenu() {
  const { user, switchOrganization, logout } = useAuth();
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

  // Close on Escape — the menu is now the only way out of the account area.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    if (open) {
      document.addEventListener("keydown", onKey);
      return () => document.removeEventListener("keydown", onKey);
    }
  }, [open]);

  if (!user) return null;

  const orgs = user.organizations ?? [];
  const activeId = user.active_organization_id;
  const activeName =
    user.active_organization?.name ||
    orgs.find((o) => o.id === activeId)?.name ||
    "No workspace";
  const contextError = user.organization_context_error;
  const displayName =
    [user.first_name, user.last_name].filter(Boolean).join(" ") || user.email || "User";

  // Backend failed to load workspace context — keep the explicit retry chip so
  // an empty org list reads as "load failed", not "no memberships".
  if (orgs.length === 0 && contextError) {
    return (
      <button
        type="button"
        onClick={() => {
          if (typeof window !== "undefined") window.location.reload();
        }}
        title={`Workspace context unavailable: ${contextError}. Click to retry.`}
        className="h-9 px-3 inline-flex items-center gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 text-xs font-medium text-amber-700 dark:text-amber-300 hover:bg-amber-500/15 cursor-pointer"
      >
        <AlertTriangle className="h-3.5 w-3.5" />
        Workspace unavailable — retry
      </button>
    );
  }

  const handleSwitch = async (id: string) => {
    if (id === activeId) {
      setOpen(false);
      return;
    }
    setSwitchingId(id);
    try {
      const result = await switchOrganization(id);
      if (result.success) {
        setOpen(false);
        if (typeof window !== "undefined") window.location.reload();
      } else {
        toast.error(result.error || "Couldn't switch workspace. Please try again.");
      }
    } catch (err: any) {
      toast.error(err?.message || "Couldn't switch workspace. Please try again.");
    } finally {
      setSwitchingId(null);
    }
  };

  const handleLogout = async () => {
    try {
      setOpen(false);
      await logout();
    } catch (error) {
      console.error("Logout error:", error);
    } finally {
      // Redirect either way — a failed logout must not strand the user signed in.
      window.location.href = "/login";
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
        aria-label={`${activeName} — account and workspace menu`}
      >
        <Building2 className="h-4 w-4 shrink-0 text-saramsa-brand" />
        <span className="max-w-[180px] truncate">{activeName}</span>
        <ChevronDown
          className={`h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform duration-200 ${
            open ? "rotate-180" : ""
          }`}
        />
      </Button>

      {open && (
        <div
          role="menu"
          className="absolute z-50 right-0 mt-2 w-72 max-w-[calc(100vw-1rem)] rounded-lg border border-border/60 bg-popover shadow-lg dark:bg-popover/95 animate-in slide-in-from-top-2 duration-200"
        >
          {/* Who you are — carried over from the avatar menu it replaces. */}
          <div className="px-3 py-2.5 border-b border-border/60 bg-secondary/50 dark:bg-secondary/30 rounded-t-lg">
            <div className="text-xs font-semibold text-popover-foreground line-clamp-1">
              {displayName}
            </div>
            <div className="text-[11px] text-muted-foreground line-clamp-1 mt-0.5">
              {user.email || "No email"}
            </div>
          </div>

          {orgs.length > 0 && (
            <>
              <div className="px-3 py-2 text-[11px] uppercase tracking-wide text-muted-foreground border-b border-border/60">
                Switch workspace
              </div>
              <div className="py-1 max-h-56 overflow-y-auto">
                {orgs.map((org) => {
                  const isActive = org.id === activeId;
                  const isSwitching = switchingId === org.id;
                  return (
                    <button
                      key={org.id}
                      role="menuitem"
                      onClick={() => handleSwitch(org.id)}
                      disabled={isSwitching}
                      className="flex items-center justify-between w-full px-3 py-2 text-sm text-foreground hover:bg-accent/60 disabled:opacity-60 cursor-pointer disabled:cursor-not-allowed"
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
            </>
          )}

          <div className="border-t border-border/60 py-1">
            <button
              role="menuitem"
              onClick={() => {
                setOpen(false);
                window.location.href = "/settings?tab=workspace";
              }}
              className="flex items-center gap-2 w-full px-3 py-2 text-sm text-foreground hover:bg-accent/60 cursor-pointer"
            >
              <Plus className="h-3.5 w-3.5 shrink-0" />
              Manage workspaces
            </button>
            <button
              role="menuitem"
              onClick={() => {
                setOpen(false);
                window.location.href = "/settings";
              }}
              className="flex items-center gap-2 w-full px-3 py-2 text-sm text-foreground hover:bg-accent/60 cursor-pointer"
            >
              <Settings className="h-3.5 w-3.5 shrink-0" />
              Settings
            </button>
          </div>

          <div className="border-t border-border/60 py-1">
            <button
              role="menuitem"
              onClick={handleLogout}
              className="flex items-center gap-2 w-full px-3 py-2 text-sm text-destructive hover:bg-accent/60 dark:hover:bg-destructive/10 cursor-pointer"
            >
              <LogOut className="h-3.5 w-3.5 shrink-0" />
              Logout
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
