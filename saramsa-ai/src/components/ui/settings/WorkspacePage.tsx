"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, ArrowRightLeft, Building2, Copy, Loader2, Mail, Save, Trash2, Users, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { apiRequest } from "@/lib/apiRequest";
import { useAuth } from "@/lib/useAuth";

type WorkspaceMember = {
  membership_id: string;
  user_id: string;
  email?: string;
  username?: string;
  first_name?: string;
  last_name?: string;
  role: string;
  status: string;
  is_current_user: boolean;
};

type WorkspacePayload = {
  organization?: {
    id: string;
    name: string;
    slug: string;
    description?: string;
  };
  current_membership?: {
    role?: string;
  };
  members: WorkspaceMember[];
};

type PendingInvite = {
  id: string;
  email: string;
  role: string;
  status: string;
  expires_at: string;
  invited_by_user_id?: string | null;
  created_at?: string;
};

export function WorkspacePage() {
  const { user } = useAuth();
  const [data, setData] = useState<WorkspacePayload>({ members: [] });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("member");
  const [renameDraft, setRenameDraft] = useState("");
  const [transferTarget, setTransferTarget] = useState("");
  const [deleteConfirm, setDeleteConfirm] = useState("");
  const [invites, setInvites] = useState<PendingInvite[]>([]);
  const [lastInviteUrl, setLastInviteUrl] = useState<string | null>(null);
  const [lastInviteEmail, setLastInviteEmail] = useState<string | null>(null);
  const [lastInviteEmailSent, setLastInviteEmailSent] = useState<boolean>(true);

  const loadMembers = async () => {
    try {
      setLoading(true);
      setError(null);
      const [memRes, invRes] = await Promise.all([
        apiRequest("get", "/auth/organizations/members/", undefined, true),
        apiRequest("get", "/auth/organizations/invites/", undefined, true).catch(() => null),
      ]);
      const payload = memRes.data?.data || { members: [] };
      setData(payload);
      setRenameDraft(payload.organization?.name || "");
      const invitesPayload = invRes?.data?.data?.invites || [];
      setInvites(invitesPayload);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || "Failed to load workspace members.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (user?.active_organization_id) {
      loadMembers();
    } else {
      setLoading(false);
    }
  }, [user?.active_organization_id]);

  const handleInvite = async () => {
    if (!inviteEmail.trim()) {
      setError("Email is required.");
      return;
    }

    try {
      setSaving(true);
      setError(null);
      setLastInviteUrl(null);
      setLastInviteEmail(null);
      const res = await apiRequest(
        "post",
        "/auth/organizations/invites/",
        { email: inviteEmail.trim(), role: inviteRole },
        true,
      );
      const invite = res.data?.data;
      const token: string | undefined = invite?.token;
      const emailSent: boolean = invite?.email_sent !== false;
      const inviteUrl = token
        ? `${window.location.origin}/register?invite=${encodeURIComponent(token)}`
        : null;
      setLastInviteUrl(inviteUrl);
      setLastInviteEmail(invite?.email || inviteEmail.trim());
      setLastInviteEmailSent(emailSent);
      setInviteEmail("");
      setInviteRole("member");
      await loadMembers();
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || "Failed to send invitation.");
    } finally {
      setSaving(false);
    }
  };

  const handleRevokeInvite = async (inviteId: string) => {
    try {
      setSaving(true);
      setError(null);
      await apiRequest(
        "delete",
        `/auth/organizations/invites/?invite_id=${encodeURIComponent(inviteId)}`,
        undefined,
        true,
      );
      await loadMembers();
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || "Failed to revoke invitation.");
    } finally {
      setSaving(false);
    }
  };

  const handleCopyInviteLink = async () => {
    if (!lastInviteUrl) return;
    try {
      await navigator.clipboard.writeText(lastInviteUrl);
    } catch (err) {
      // Clipboard API may be blocked in insecure contexts or when the
      // tab isn't focused. Logged so it isn't completely invisible — the
      // URL is still visible in the panel for manual copy.
      if (typeof console !== "undefined") {
        console.warn("WorkspacePage: clipboard copy failed", err);
      }
    }
  };

  const handleRemove = async (memberUserId: string) => {
    try {
      setSaving(true);
      setError(null);
      const res = await apiRequest(
        "delete",
        `/auth/organizations/members/?user_id=${encodeURIComponent(memberUserId)}`,
        undefined,
        true,
      );
      setData(res.data?.data || { members: [] });
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || "Failed to remove member.");
    } finally {
      setSaving(false);
    }
  };

  const handleRename = async () => {
    const cleaned = renameDraft.trim();
    if (!cleaned || cleaned === data.organization?.name) return;
    try {
      setSaving(true);
      setError(null);
      const res = await apiRequest(
        "patch", "/auth/organizations/current/", { name: cleaned }, true,
      );
      const updatedOrg = res.data?.data?.organization;
      if (updatedOrg) {
        setData((current) => ({ ...current, organization: updatedOrg }));
      }
      // Reload to refresh membership view; useAuth will refresh user via /me
      await loadMembers();
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || "Failed to rename workspace.");
    } finally {
      setSaving(false);
    }
  };

  const handleTransfer = async () => {
    if (!transferTarget) return;
    if (!confirm("Transfer ownership of this workspace? You will be demoted to admin.")) return;
    try {
      setSaving(true);
      setError(null);
      const res = await apiRequest(
        "post", "/auth/organizations/transfer/", { new_owner_user_id: transferTarget }, true,
      );
      setData(res.data?.data || data);
      setTransferTarget("");
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || "Failed to transfer ownership.");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (deleteConfirm !== data.organization?.name) {
      setError("Type the workspace name exactly to confirm deletion.");
      return;
    }
    try {
      setSaving(true);
      setError(null);
      await apiRequest("delete", "/auth/organizations/current/", undefined, true);
      // Hard reload: active org reassignment + token rotation happened
      // server-side; easiest way to re-bootstrap state cleanly is a refresh.
      window.location.href = "/settings?tab=workspace";
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || "Failed to delete workspace.");
      setSaving(false);
    }
  };

  const canManageMembers = ["owner", "admin"].includes(data.current_membership?.role || "");
  const isOwner = data.current_membership?.role === "owner";
  const transferableMembers = data.members.filter(
    (m) => !m.is_current_user && m.role !== "owner",
  );

  return (
    <div className="space-y-8">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-foreground">Workspace</h2>
          <p className="text-sm text-muted-foreground mt-2">Manage the active organization and its members.</p>
        </div>
        <div className="inline-flex items-center gap-2 rounded-md border border-border bg-secondary/60 px-2.5 py-1.5 text-xs font-medium text-foreground">
          <Building2 className="h-3.5 w-3.5 text-muted-foreground" />
          <span className="truncate max-w-[12rem]">{user?.active_organization?.name || "No workspace selected"}</span>
        </div>
      </header>

      {error && (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {canManageMembers && data.organization && (
        <section className="rounded-lg border border-border bg-card">
          <div className="border-b border-border px-6 py-4">
            <h3 className="text-sm font-medium text-foreground">Workspace name</h3>
            <p className="text-xs text-muted-foreground mt-1">Visible to every member in the navbar and switcher.</p>
          </div>
          <div className="px-6 py-5">
            <div className="grid grid-cols-1 md:grid-cols-[1fr_auto] gap-3">
              <input
                value={renameDraft}
                onChange={(e) => setRenameDraft(e.target.value)}
                className="h-10 rounded-md border border-border bg-background px-3 text-sm text-foreground outline-none focus:border-ring focus:ring-2 focus:ring-ring/30"
              />
              <Button
                onClick={handleRename}
                disabled={saving || !renameDraft.trim() || renameDraft.trim() === data.organization.name}
                variant="saramsa"
                className="h-10 flex items-center gap-2"
              >
                {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                Save
              </Button>
            </div>
          </div>
        </section>
      )}

      {canManageMembers && (
        <section className="rounded-lg border border-border bg-card">
          <div className="border-b border-border px-6 py-4">
            <h3 className="text-sm font-medium text-foreground">Invite by email</h3>
            <p className="text-xs text-muted-foreground mt-1">
              They'll receive an invite link to create an account (or sign in) and join this workspace.
            </p>
          </div>
          <div className="px-6 py-5 space-y-5">
            <div className="grid grid-cols-1 md:grid-cols-[1fr_180px_auto] gap-3">
              <input
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                placeholder="teammate@example.com"
                className="h-10 rounded-md border border-border bg-background px-3 text-sm text-foreground outline-none focus:border-ring focus:ring-2 focus:ring-ring/30"
              />
              <select
                value={inviteRole}
                onChange={(e) => setInviteRole(e.target.value)}
                className="h-10 rounded-md border border-border bg-background px-3 text-sm text-foreground outline-none focus:border-ring focus:ring-2 focus:ring-ring/30"
              >
                <option value="viewer">Viewer</option>
                <option value="member">Member</option>
                <option value="admin">Admin</option>
              </select>
              <Button onClick={handleInvite} disabled={saving} variant="saramsa" className="h-10 flex items-center gap-2">
                {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Mail className="h-4 w-4" />}
                Send invite
              </Button>
            </div>

            {lastInviteUrl && lastInviteEmail && (
              <div className="rounded-md border border-saramsa-brand/30 bg-saramsa-brand/5 px-3 py-3 space-y-2">
                <p className="text-xs font-medium text-foreground">
                  Invite sent to <span className="font-semibold">{lastInviteEmail}</span>
                  {lastInviteEmailSent ? '.' : '. (Email delivery failed — share the link below manually.)'}
                </p>
                <div className="flex items-center gap-2">
                  <code className="flex-1 truncate rounded border border-border bg-background px-2 py-1.5 text-xs text-muted-foreground">
                    {lastInviteUrl}
                  </code>
                  <Button
                    type="button"
                    variant="outline"
                    className="h-8 px-2"
                    onClick={handleCopyInviteLink}
                    title="Copy invite link"
                  >
                    <Copy className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>
            )}
          </div>
        </section>
      )}

      {canManageMembers && invites.length > 0 && (
        <section className="rounded-lg border border-border bg-card">
          <div className="flex items-center gap-2 border-b border-border px-6 py-4">
            <Mail className="h-4 w-4 text-muted-foreground" />
            <h3 className="text-sm font-medium text-foreground">Pending invites</h3>
          </div>
          <ul className="divide-y divide-border">
            {invites.map((inv) => (
              <li key={inv.id} className="flex items-center justify-between gap-4 px-6 py-4">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-foreground truncate">{inv.email}</p>
                  <p className="text-xs text-muted-foreground">
                    {inv.role} · expires {new Date(inv.expires_at).toLocaleDateString()}
                  </p>
                </div>
                <Button
                  onClick={() => handleRevokeInvite(inv.id)}
                  disabled={saving}
                  variant="ghost"
                  className="h-8 px-2 text-destructive hover:bg-destructive/10"
                  title="Revoke invitation"
                >
                  <X className="h-4 w-4" />
                </Button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="rounded-lg border border-border bg-card">
        <div className="flex items-center gap-2 border-b border-border px-6 py-4">
          <Users className="h-4 w-4 text-muted-foreground" />
          <h3 className="text-sm font-medium text-foreground">Members</h3>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-10">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <ul className="divide-y divide-border">
            {data.members.map((member) => {
              const displayName =
                `${member.first_name || ""} ${member.last_name || ""}`.trim() ||
                member.username ||
                member.email ||
                member.user_id;
              return (
                <li key={member.membership_id} className="flex items-center justify-between gap-4 px-6 py-4">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-foreground truncate">{displayName}</p>
                    <p className="text-xs text-muted-foreground truncate">{member.email || member.user_id}</p>
                  </div>
                  <div className="flex items-center gap-3 flex-shrink-0">
                    <span className="rounded-full bg-secondary px-2.5 py-0.5 text-xs font-medium capitalize text-foreground">
                      {member.role}
                    </span>
                    {member.is_current_user && (
                      <span className="text-xs text-muted-foreground">You</span>
                    )}
                    {canManageMembers && member.role !== "owner" && !member.is_current_user && (
                      <Button
                        onClick={() => handleRemove(member.user_id)}
                        disabled={saving}
                        variant="ghost"
                        className="h-8 px-2 text-destructive hover:bg-destructive/10"
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    )}
                  </div>
                </li>
              );
            })}
            {!data.members.length && (
              <li className="px-6 py-10 text-sm text-muted-foreground">No members found for this workspace.</li>
            )}
          </ul>
        )}
      </section>

      {isOwner && data.organization && (
        <section className="rounded-lg border border-destructive/30 bg-destructive/5">
          <div className="flex items-center gap-2 border-b border-destructive/30 px-6 py-4">
            <AlertTriangle className="h-4 w-4 text-destructive" />
            <h3 className="text-sm font-semibold text-destructive">Danger zone</h3>
          </div>

          <div className="px-6 py-5 space-y-6 divide-y divide-destructive/20">
            <div className="space-y-4">
              <div>
                <p className="text-sm font-medium text-foreground">Transfer ownership</p>
                <p className="text-xs text-muted-foreground mt-1">
                  Hand this workspace to another member. You will be demoted to admin.
                </p>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-[1fr_auto] gap-3">
                <select
                  value={transferTarget}
                  onChange={(e) => setTransferTarget(e.target.value)}
                  disabled={transferableMembers.length === 0}
                  className="h-10 rounded-md border border-border bg-background px-3 text-sm text-foreground outline-none focus:border-ring focus:ring-2 focus:ring-ring/30 disabled:opacity-60"
                >
                  <option value="">{transferableMembers.length === 0 ? "No eligible members" : "Choose a new owner"}</option>
                  {transferableMembers.map((m) => (
                    <option key={m.user_id} value={m.user_id}>
                      {(m.first_name || m.last_name) ? `${m.first_name || ""} ${m.last_name || ""}`.trim() : m.email || m.user_id}
                      {m.email ? ` (${m.email})` : ""}
                    </option>
                  ))}
                </select>
                <Button
                  onClick={handleTransfer}
                  disabled={saving || !transferTarget}
                  variant="outline"
                  className="h-10 flex items-center gap-2"
                >
                  {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowRightLeft className="h-4 w-4" />}
                  Transfer
                </Button>
              </div>
            </div>

            <div className="pt-6 space-y-4">
              <div>
                <p className="text-sm font-medium text-destructive">Delete workspace</p>
                <p className="text-xs text-muted-foreground mt-1">
                  Permanently removes this workspace and every project, integration, and credit balance attached to it. This cannot be undone.
                </p>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-[1fr_auto] gap-3">
                <input
                  value={deleteConfirm}
                  onChange={(e) => setDeleteConfirm(e.target.value)}
                  placeholder={`Type "${data.organization.name}" to confirm`}
                  className="h-10 rounded-md border border-destructive/40 bg-background px-3 text-sm text-foreground outline-none focus:border-destructive focus:ring-2 focus:ring-destructive/30"
                />
                <Button
                  onClick={handleDelete}
                  disabled={saving || deleteConfirm !== data.organization.name}
                  variant="ghost"
                  className="h-10 flex items-center gap-2 text-destructive hover:bg-destructive/10 disabled:opacity-50"
                >
                  {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                  Delete workspace
                </Button>
              </div>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
