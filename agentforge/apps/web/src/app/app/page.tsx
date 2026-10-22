"use client";

import {
  Activity,
  ArrowRight,
  BadgeCheck,
  CircleAlert,
  CircleCheck,
  CircleDashed,
  ClipboardCheck,
  Cpu,
  FileWarning,
  Library,
  Loader,
  MessageSquareText,
  Plus,
  SearchX,
  ShieldCheck,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { type Stage, PipelineFlow } from "@/components/dashboard/pipeline-flow";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { StatTile } from "@/components/ui/stat-tile";
import { formatAge } from "@/lib/format";
import {
  ACTIVITY_LABEL,
  type Overview as OverviewData,
  provenShare,
  useOverview,
} from "@/lib/overview";
import { useReviewQueue } from "@/lib/reviews";
import { useSession, useSignOut } from "@/lib/session";

const number = new Intl.NumberFormat("en");

function pipelineStages(data: OverviewData): Stage[] {
  const p = data.pipeline;
  return [
    { key: "pending", label: "Waiting", count: p.pending ?? 0, icon: CircleDashed },
    { key: "answering", label: "Answering", count: p.answering ?? 0, icon: Loader, live: true },
    { key: "answered", label: "Answered", count: p.answered ?? 0, icon: CircleCheck, tone: "ok" },
    {
      key: "insufficient",
      label: "No evidence",
      count: p.insufficient_evidence ?? 0,
      icon: SearchX,
      tone: "warn",
    },
    {
      key: "review",
      label: "To review",
      count: data.answers_to_review,
      icon: ClipboardCheck,
      tone: "brand",
    },
  ];
}

function ModelStatusRow({ data }: { data: OverviewData }) {
  const model = data.model;
  if (!model) return null;
  if (model.provider === "fake") {
    return <Badge tone="warn">Fake models (demo mode)</Badge>;
  }
  const ready = model.reachable && model.chat_model_installed && model.embedding_model_installed;
  return (
    <div className="space-y-2 text-sm">
      <div className="flex items-center justify-between">
        <span className="text-muted">Ollama</span>
        <Badge tone={model.reachable ? "ok" : "danger"}>
          {model.reachable ? "Reachable" : "Not reachable"}
        </Badge>
      </div>
      {[
        ["Chat", model.chat_model, model.chat_model_installed],
        ["Embeddings", model.embedding_model, model.embedding_model_installed],
      ].map(([role, name, installed]) => (
        <div key={String(role)} className="flex items-center justify-between gap-3">
          <span className="truncate text-muted">
            {role}: <span className="text-fg">{name}</span>
          </span>
          <Badge tone={installed ? "ok" : "danger"}>{installed ? "Installed" : "Missing"}</Badge>
        </div>
      ))}
      {!ready && model.reachable && (
        <p className="text-xs text-muted">Run: ollama pull {model.chat_model}</p>
      )}
    </div>
  );
}

export default function Overview() {
  const router = useRouter();
  const { data: user } = useSession();
  const signOut = useSignOut();
  const overview = useOverview();
  const queue = useReviewQueue();
  if (!user) return null;
  const data = overview.data;
  const running = data ? (data.runs_by_status.running ?? 0) + (data.runs_by_status.queued ?? 0) : 0;
  const proven = data ? provenShare(data.sentences_30d) : null;
  const flaggedDocs = data?.documents_flagged ?? 0;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Overview</h1>
          <p className="mt-1 text-sm text-muted">
            Signed in as {user.email} ({user.role}
            {user.can_review ? ", reviewer" : ""}).
          </p>
        </div>
        <div className="flex gap-2">
          <Link href="/app/ask" className={buttonVariants({ variant: "secondary" })}>
            <MessageSquareText className="size-4" aria-hidden /> Ask
          </Link>
          <Link href="/app/runs/new" className={buttonVariants()}>
            <Plus className="size-4" aria-hidden /> New questionnaire
          </Link>
        </div>
      </div>

      {overview.isError && (
        <p role="alert" className="text-sm text-danger">
          Could not load the overview: {overview.error.message}
        </p>
      )}

      <section aria-label="Key numbers" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile
          icon={Activity}
          label="Runs in progress"
          value={data ? running : "–"}
          hint={data ? `${data.runs_by_status.succeeded ?? 0} finished` : undefined}
        />
        <StatTile
          icon={ClipboardCheck}
          label="Answers to review"
          value={data ? data.answers_to_review : "–"}
          hint={
            data
              ? `${data.reviews_open} questionnaire${data.reviews_open === 1 ? "" : "s"}`
              : undefined
          }
          tone={data && data.answers_to_review > 0 ? "warn" : "brand"}
        />
        <StatTile
          icon={BadgeCheck}
          label="Proven word for word"
          value={proven === null ? "–" : `${proven}%`}
          hint={
            data
              ? `of ${number.format(Object.values(data.sentences_30d).reduce((a, b) => a + b, 0))} sentences, last 30 days`
              : undefined
          }
          tone="ok"
        />
        <StatTile
          icon={Cpu}
          label="Model calls, 7 days"
          value={data ? number.format(data.model_calls_7d) : "–"}
          hint={
            data
              ? `${number.format(data.tokens_7d)} tokens${data.call_seconds_p50_7d !== null ? ` · ${data.call_seconds_p50_7d} s median` : ""}`
              : undefined
          }
        />
      </section>

      <div className="grid gap-6 xl:grid-cols-[1.6fr_1fr]">
        <Card>
          <CardHeader
            title="Questionnaire pipeline"
            action={running > 0 ? <Badge tone="progress">Live</Badge> : <Badge>Idle</Badge>}
          />
          <p className="mt-1 text-sm text-muted">
            Questions of the runs in progress, by stage, and the answers waiting for a person.
          </p>
          <div className="mt-6">
            {data ? (
              <PipelineFlow stages={pipelineStages(data)} label="Questionnaire pipeline" />
            ) : (
              <p className="text-sm text-muted" role="status">
                Loading…
              </p>
            )}
          </div>
          {data && running === 0 && data.answers_to_review === 0 && (
            <p className="mt-6 text-sm text-muted">
              Nothing is running.{" "}
              <Link href="/app/runs/new" className="text-accent underline">
                Answer a questionnaire
              </Link>
              .
            </p>
          )}
        </Card>

        <Card>
          <CardHeader
            title="Review queue"
            action={
              <Link href="/app/reviews" className="text-sm text-accent hover:underline">
                View all
              </Link>
            }
          />
          <ul className="mt-4 space-y-2">
            {(queue.data ?? []).slice(0, 4).map((entry) => (
              <li key={entry.run_id}>
                <Link
                  href={`/app/reviews/${entry.run_id}`}
                  className="flex items-center justify-between gap-3 rounded-xl border border-border/70 bg-surface-2/40 px-3 py-2.5 transition-colors hover:border-accent/50"
                >
                  <span className="min-w-0 truncate text-sm font-medium">{entry.title}</span>
                  <Badge tone={entry.undecided > 0 ? "warn" : "ok"}>
                    {entry.undecided > 0 ? `${entry.undecided} to decide` : "Ready to complete"}
                  </Badge>
                </Link>
              </li>
            ))}
          </ul>
          {queue.data?.length === 0 && (
            <p className="mt-4 text-sm text-muted">No answers are waiting for review.</p>
          )}
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title="Recent activity" />
          <ul className="mt-4 divide-y divide-border/60">
            {(data?.activity ?? []).map((event) => (
              <li key={`${event.run_id}-${event.type}-${event.at}`}>
                <Link
                  href={`/app/runs/${event.run_id}`}
                  className="flex items-center gap-3 py-2.5 text-sm hover:text-accent"
                >
                  <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-accent/10 text-accent">
                    {event.type === "run_failed" ? (
                      <CircleAlert className="size-4 text-danger" aria-hidden />
                    ) : (
                      <ArrowRight className="size-4" aria-hidden />
                    )}
                  </span>
                  <span className="min-w-0 flex-1 truncate">
                    <span className="font-medium">{ACTIVITY_LABEL[event.type] ?? event.type}</span>
                    <span className="text-muted"> · {event.title}</span>
                  </span>
                  <span className="shrink-0 text-xs text-muted">{formatAge(event.at)}</span>
                </Link>
              </li>
            ))}
          </ul>
          {data && data.activity.length === 0 && (
            <p className="mt-4 text-sm text-muted">No questionnaire runs yet.</p>
          )}
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader title="Models" />
            <div className="mt-4">{data ? <ModelStatusRow data={data} /> : null}</div>
          </Card>
          <Card>
            <CardHeader
              title="Knowledge"
              action={
                <Link href="/app/knowledge" className="text-sm text-accent hover:underline">
                  Open
                </Link>
              }
            />
            {data && (
              <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
                <div className="flex items-center gap-2">
                  <Library className="size-4 text-accent" aria-hidden />
                  <dt className="text-muted">Bases</dt>
                  <dd className="ml-auto font-semibold tabular-nums">{data.knowledge_bases}</dd>
                </div>
                <div className="flex items-center gap-2">
                  <CircleCheck className="size-4 text-ok" aria-hidden />
                  <dt className="text-muted">Ready</dt>
                  <dd className="ml-auto font-semibold tabular-nums">
                    {data.documents_by_status.ready ?? 0}
                  </dd>
                </div>
                <div className="col-span-2 flex items-center gap-2">
                  <FileWarning
                    className={flaggedDocs > 0 ? "size-4 text-warn" : "size-4 text-muted"}
                    aria-hidden
                  />
                  <dt className="text-muted">Flagged as suspicious</dt>
                  <dd className="ml-auto font-semibold tabular-nums">{flaggedDocs}</dd>
                </div>
              </dl>
            )}
          </Card>
          <Card>
            <CardHeader title="Security" />
            <p className="mt-2 text-sm text-muted">
              Signed in on a device you no longer use? Sign out of every session at once.
            </p>
            <Button
              variant="secondary"
              className="mt-3"
              onClick={async () => {
                await signOut(true);
                router.replace("/login");
              }}
            >
              <ShieldCheck className="size-4" aria-hidden /> Sign out everywhere
            </Button>
          </Card>
        </div>
      </div>
    </div>
  );
}
