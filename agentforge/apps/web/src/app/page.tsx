import {
  ArrowRight,
  BadgeCheck,
  ClipboardList,
  FileSearch,
  HardDrive,
  Lock,
  type LucideIcon,
  Plug,
  ScrollText,
  ShieldCheck,
  Upload,
  UserCheck,
} from "lucide-react";
import Link from "next/link";

import { Logo } from "@/components/brand/logo";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const REPO = "https://github.com/Kingslee1604/2026AIProjects/tree/feature/dev/agentforge";

const POINTS = ["Every sentence quoted", "Runs on your laptop", "A person approves"];

const STEPS: { icon: LucideIcon; title: string; text: string }[] = [
  {
    icon: Upload,
    title: "Upload your policies",
    text: "PDF, Word, or Markdown. Indexed for meaning and keywords, inside Postgres.",
  },
  {
    icon: ClipboardList,
    title: "Ask, or drop in a questionnaire",
    text: "One question, or a whole spreadsheet answered in a durable background run.",
  },
  {
    icon: FileSearch,
    title: "Every claim is checked",
    text: "Each sentence must quote its source word for word, or an AI check must confirm it.",
  },
  {
    icon: UserCheck,
    title: "A reviewer decides",
    text: "Approve, edit, or reject with the keyboard, then download the completed file.",
  },
];

const TRUST: { icon: LucideIcon; title: string; text: string }[] = [
  {
    icon: HardDrive,
    title: "Your machine, your models",
    text: "Open-weight models through Ollama. No paid API, no document leaves the laptop.",
  },
  {
    icon: ShieldCheck,
    title: "Says when it cannot answer",
    text: "No passage close enough, or no quote that proves it: the answer is “insufficient evidence”.",
  },
  {
    icon: Lock,
    title: "Isolated and audited",
    text: "Row-level security per organization, scoped API keys, and an audit log of every decision.",
  },
  {
    icon: Plug,
    title: "Works with AI assistants",
    text: "A read-only MCP server lets assistants search your documents and get cited answers.",
  },
];

/** A static example in the style of the Ask page: the demo corpus, not live data. */
function ExampleAnswer() {
  return (
    <figure className="panel glow overflow-hidden" aria-label="Example answer">
      <div className="flex items-center gap-2 border-b border-border/70 px-5 py-3">
        <span className="size-2.5 rounded-full bg-danger/70" aria-hidden />
        <span className="size-2.5 rounded-full bg-warn/70" aria-hidden />
        <span className="size-2.5 rounded-full bg-ok/70" aria-hidden />
        <span className="ml-3 text-xs text-muted">Example · Northwind demo documents</span>
      </div>
      <div className="space-y-4 p-5">
        <div>
          <p className="text-xs tracking-wide text-muted uppercase">Question</p>
          <p className="mt-1 font-medium">How long are access logs kept, and are keys rotated?</p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-2">
            {[
              "Access logs are retained for ninety days.",
              "Encryption keys are rotated every twelve months.",
            ].map((sentence, i) => (
              <div
                key={sentence}
                className={cn(
                  "rounded-xl border p-3 text-sm",
                  i === 0 ? "border-accent/60 bg-accent/10" : "border-border bg-surface-2/50",
                )}
              >
                {sentence}
                <span className="mt-2 flex items-center gap-1 text-xs text-ok">
                  <BadgeCheck className="size-3.5" aria-hidden /> Verified
                </span>
              </div>
            ))}
          </div>
          <blockquote className="rounded-xl border border-border bg-bg/60 p-3 text-sm">
            <p className="text-xs text-muted">security-policy.md · p. 1</p>
            <p className="mt-2 text-muted">
              <mark className="rounded bg-accent/25 px-0.5 text-fg">
                Access logs are retained for ninety days.
              </mark>{" "}
              Logs are written to immutable storage and reviewed weekly by the security team.
            </p>
          </blockquote>
        </div>
      </div>
    </figure>
  );
}

export default function Landing() {
  return (
    <div className="min-h-screen">
      <header className="mx-auto flex max-w-6xl items-center justify-between px-6 py-5">
        <Link href="/" aria-label="AgentForge home" className="rounded-lg">
          <Logo />
        </Link>
        <nav aria-label="Site" className="flex items-center gap-2 text-sm">
          <a href="#how" className="hidden rounded-lg px-3 py-2 text-muted hover:text-fg sm:block">
            How it works
          </a>
          <a
            href="#trust"
            className="hidden rounded-lg px-3 py-2 text-muted hover:text-fg sm:block"
          >
            Security
          </a>
          <a href={REPO} className="hidden rounded-lg px-3 py-2 text-muted hover:text-fg sm:block">
            GitHub
          </a>
          <Link href="/app" className={buttonVariants({ size: "md" })}>
            Open AgentForge
          </Link>
        </nav>
      </header>

      <main>
        <section className="mx-auto grid max-w-6xl items-center gap-12 px-6 pt-10 pb-20 lg:grid-cols-[1.05fr_1fr] lg:pt-20">
          <div>
            <p className="text-sm font-medium text-accent">Self-hosted AI for evidence work</p>
            <h1 className="mt-4 text-4xl leading-[1.08] font-semibold tracking-tight sm:text-6xl">
              Answers your auditors can <span className="text-brand">verify</span>
            </h1>
            <p className="mt-6 max-w-xl text-lg text-muted">
              AgentForge answers security questionnaires and questions about your policies from your
              own documents. Every sentence shows its source; anything it cannot prove, it says so;
              and a person approves what goes out.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link href="/app" className={buttonVariants({ size: "lg" })}>
                Get started <ArrowRight className="size-4" aria-hidden />
              </Link>
              <a href={REPO} className={buttonVariants({ size: "lg", variant: "secondary" })}>
                <ScrollText className="size-4" aria-hidden /> Read the docs
              </a>
            </div>
            <ul className="mt-8 flex flex-wrap gap-x-6 gap-y-2 text-sm text-muted">
              {POINTS.map((point) => (
                <li key={point} className="flex items-center gap-2">
                  <BadgeCheck className="size-4 text-accent" aria-hidden />
                  {point}
                </li>
              ))}
            </ul>
          </div>
          <ExampleAnswer />
        </section>

        <section id="how" className="mx-auto max-w-6xl px-6 py-16">
          <h2 className="text-3xl font-semibold tracking-tight">How it works</h2>
          <ol className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {STEPS.map(({ icon: Icon, title, text }, index) => (
              <li key={title} className="panel p-5">
                <div className="flex items-center gap-3">
                  <span className="bg-brand flex size-10 items-center justify-center rounded-xl text-accent-fg">
                    <Icon className="size-5" aria-hidden />
                  </span>
                  <span className="text-xs font-medium text-muted">Step {index + 1}</span>
                </div>
                <h3 className="mt-4 font-semibold">{title}</h3>
                <p className="mt-2 text-sm text-muted">{text}</p>
              </li>
            ))}
          </ol>
        </section>

        <section id="trust" className="mx-auto max-w-6xl px-6 py-16">
          <h2 className="text-3xl font-semibold tracking-tight">Built to be trusted</h2>
          <ul className="mt-8 grid gap-4 sm:grid-cols-2">
            {TRUST.map(({ icon: Icon, title, text }) => (
              <li key={title} className="panel flex gap-4 p-5">
                <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-accent/15 text-accent">
                  <Icon className="size-5" aria-hidden />
                </span>
                <div>
                  <h3 className="font-semibold">{title}</h3>
                  <p className="mt-1 text-sm text-muted">{text}</p>
                </div>
              </li>
            ))}
          </ul>
        </section>
      </main>

      <footer className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 border-t border-border/70 px-6 py-8 text-sm text-muted">
        <span>AgentForge · Apache-2.0</span>
        <a href={REPO} className="hover:text-fg">
          Source on GitHub
        </a>
      </footer>
    </div>
  );
}
