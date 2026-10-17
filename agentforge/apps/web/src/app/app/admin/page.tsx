"use client";

import { useQuery } from "@tanstack/react-query";

import { Card, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api/client";

export default function AdminPage() {
  const users = useQuery({
    queryKey: ["admin", "users"],
    queryFn: async () => (await api.GET("/v1/admin/users")).data ?? [],
  });
  const audit = useQuery({
    queryKey: ["admin", "audit"],
    queryFn: async () =>
      (await api.GET("/v1/admin/audit", { params: { query: { limit: 50 } } })).data ?? [],
  });

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Admin</h1>
      <Card>
        <CardTitle>Members</CardTitle>
        <p className="mt-1 text-sm text-muted">
          Create accounts with <code>make user-create</code> (there is no public sign-up).
        </p>
        <table className="mt-4 w-full text-sm">
          <thead className="text-left text-muted">
            <tr>
              <th className="py-2 font-medium">Email</th>
              <th className="py-2 font-medium">Role</th>
              <th className="py-2 font-medium">Reviewer</th>
              <th className="py-2 font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {users.data?.map((u) => (
              <tr key={u.id} className="border-t border-border">
                <td className="py-2">{u.email}</td>
                <td className="py-2">{u.role}</td>
                <td className="py-2">{u.can_review ? "Yes" : "No"}</td>
                <td className="py-2">{u.disabled ? "Disabled" : "Active"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Card>
        <CardTitle>Recent security events</CardTitle>
        <ul className="mt-4 divide-y divide-border text-sm">
          {audit.data?.map((e) => (
            <li key={e.id} className="flex flex-wrap justify-between gap-2 py-2">
              <span>
                <span className="font-medium">{e.action}</span>{" "}
                <span className={e.outcome === "success" ? "text-ok" : "text-danger"}>
                  {e.outcome}
                </span>{" "}
                <span className="text-muted">{e.actor_email ?? "—"}</span>
              </span>
              <time className="text-muted" dateTime={e.at}>
                {new Date(e.at).toLocaleString()}
              </time>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
