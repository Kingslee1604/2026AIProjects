"use client";

import { type FormEvent, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { ConfirmButton } from "@/components/ui/confirm-button";
import { Input, Label } from "@/components/ui/input";
import {
  EXPIRY_DAYS,
  keyState,
  SCOPES,
  type Scope,
  useApiKeys,
  useCreateApiKey,
  useRevokeApiKey,
} from "@/lib/api-keys";
import { formatAge } from "@/lib/format";

const STATE = {
  active: { label: "Active", tone: "ok" },
  revoked: { label: "Revoked", tone: "neutral" },
  expired: { label: "Expired", tone: "warn" },
} as const;

/** Workspace API keys: create (the key is shown once), see last use, revoke. */
export function ApiKeys() {
  const keys = useApiKeys();
  const create = useCreateApiKey();
  const revoke = useRevokeApiKey();
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<Set<Scope>>(new Set(["api:read"]));
  const [days, setDays] = useState<number>(90);
  const [shown, setShown] = useState<{ name: string; key: string } | null>(null);
  const [copied, setCopied] = useState(false);

  function toggle(scope: Scope) {
    const next = new Set(scopes);
    if (next.has(scope)) next.delete(scope);
    else next.add(scope);
    setScopes(next);
  }

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    const created = await create
      .mutateAsync({ name: name.trim(), scopes: [...scopes], expires_in_days: days })
      .catch(() => null);
    if (created) {
      setShown({ name: created.api_key.name, key: created.key });
      setCopied(false);
      setName("");
    }
  }

  async function copy(key: string) {
    await navigator.clipboard.writeText(key).catch(() => undefined);
    setCopied(true);
  }

  return (
    <Card>
      <CardTitle>API keys</CardTitle>
      <p className="mt-1 text-sm text-muted">
        Let other programs use AgentForge. Send a key as{" "}
        <code className="text-xs">Authorization: Bearer &lt;key&gt;</code>. A key acts for the
        person who created it, limited to its scopes, and can never manage keys, members, or
        reviews.
      </p>

      {shown && (
        <div
          role="status"
          className="mt-4 rounded-md border border-warn/50 bg-bg p-4 text-sm"
          data-testid="new-key"
        >
          <p className="font-medium">
            Key “{shown.name}” created. Copy it now: it is shown only once.
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <code
              className="min-w-0 flex-1 rounded bg-surface px-2 py-1 text-xs break-all"
              data-testid="new-key-value"
            >
              {shown.key}
            </code>
            <Button size="sm" variant="secondary" onClick={() => void copy(shown.key)}>
              {copied ? "Copied" : "Copy"}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setShown(null)}>
              Done
            </Button>
          </div>
        </div>
      )}

      <form className="mt-4 grid gap-3 sm:max-w-xl" onSubmit={onCreate} aria-label="New API key">
        <div className="grid gap-1">
          <Label htmlFor="key-name">Name</Label>
          <Input
            id="key-name"
            required
            maxLength={100}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Customer portal"
          />
        </div>
        <fieldset>
          <legend className="text-sm font-medium">Allowed to</legend>
          <div className="mt-1 space-y-1">
            {SCOPES.map((s) => (
              <label key={s.value} className="flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-0.5 size-4 accent-accent"
                  checked={scopes.has(s.value)}
                  onChange={() => toggle(s.value)}
                />
                <span>
                  <span className="font-medium">{s.label}</span>{" "}
                  <span className="text-muted">
                    ({s.value}): {s.help}
                  </span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>
        <div className="grid gap-1 sm:max-w-48">
          <Label htmlFor="key-expiry">Expires after</Label>
          <select
            id="key-expiry"
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="h-9 rounded-lg border border-border bg-surface-2/60 px-2 text-sm"
          >
            {EXPIRY_DAYS.map((d) => (
              <option key={d} value={d}>
                {d} days
              </option>
            ))}
          </select>
        </div>
        {create.isError && (
          <p className="text-sm text-danger" role="alert">
            {create.error.message}
          </p>
        )}
        <div>
          <Button type="submit" disabled={create.isPending || !name.trim() || scopes.size === 0}>
            {create.isPending ? "Creating…" : "Create key"}
          </Button>
        </div>
      </form>

      {revoke.isError && (
        <p className="mt-3 text-sm text-danger" role="alert">
          {revoke.error.message}
        </p>
      )}
      {(keys.data?.length ?? 0) > 0 && (
        <table className="mt-5 w-full text-sm">
          <caption className="sr-only">API keys</caption>
          <thead className="text-left text-muted">
            <tr>
              <th scope="col" className="py-2 pr-3 font-medium">
                Name
              </th>
              <th scope="col" className="py-2 pr-3 font-medium">
                Scopes
              </th>
              <th scope="col" className="py-2 pr-3 font-medium">
                Last used
              </th>
              <th scope="col" className="py-2 pr-3 font-medium">
                Expires
              </th>
              <th scope="col" className="py-2 font-medium">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {keys.data?.map((k) => {
              const state = STATE[keyState(k)];
              return (
                <tr
                  key={k.id}
                  className="border-t border-border align-top"
                  data-testid="api-key-row"
                >
                  <td className="py-2 pr-3">
                    <span className="font-medium">{k.name}</span>{" "}
                    <Badge tone={state.tone}>{state.label}</Badge>
                    <p className="text-xs text-muted">
                      <code>af_{k.prefix}_…</code> · by {k.created_by_email}
                    </p>
                  </td>
                  <td className="py-2 pr-3 text-muted">{k.scopes.join(", ")}</td>
                  <td className="py-2 pr-3 whitespace-nowrap text-muted">
                    {k.last_used_at ? (
                      <time dateTime={k.last_used_at}>{formatAge(k.last_used_at)}</time>
                    ) : (
                      "never"
                    )}
                  </td>
                  <td className="py-2 pr-3 whitespace-nowrap text-muted">
                    <time dateTime={k.expires_at}>
                      {new Date(k.expires_at).toLocaleDateString()}
                    </time>
                  </td>
                  <td className="py-2 text-right">
                    {keyState(k) === "active" && (
                      <ConfirmButton
                        label="Revoke"
                        confirmLabel="Revoke"
                        backLabel="Keep"
                        pendingLabel="Revoking…"
                        question={`Revoke ${k.name}? Programs using it stop working at once.`}
                        pending={revoke.isPending && revoke.variables === k.id}
                        onConfirm={() => revoke.mutate(k.id)}
                      />
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </Card>
  );
}
