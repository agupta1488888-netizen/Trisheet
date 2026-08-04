"use client";

/**
 * The assistant showcase.
 *
 * Sits below the input screen's full-bleed hero, in the same "premium SaaS"
 * exception zone CLAUDE.md carves out for `components/input/` — gradients,
 * rounded-2xl cards and a dark cinematic backdrop are permitted here in a way
 * they never are under `components/report/`.
 *
 * The copy describes exactly what Part 1 shipped and nothing more: answers
 * grounded in the report's own filed data, every claim cited, an honest
 * "not found" when a filing doesn't say. No web search, no valuation,
 * nothing this pass doesn't actually do.
 */

import dynamic from "next/dynamic";

import { useReducedMotion } from "@/hooks/use-reduced-motion";

const AgentCanvas = dynamic(
  () =>
    import("@/components/input/ai-financial-agent-canvas").then(
      (mod) => mod.AgentCanvas,
    ),
  { ssr: false },
);

const CAPABILITIES: readonly { heading: string; body: string }[] = [
  {
    heading: "Grounded in filed data",
    body: "Every answer draws on the same SEC filings the report itself was built from — nothing looked up elsewhere.",
  },
  {
    heading: "Always cites its source",
    body: "A claim carries the filing, the accession number and the date it was filed, the same way every figure in the report does.",
  },
  {
    heading: "Says “not found”",
    body: "When a filing doesn't disclose something, the answer says so plainly instead of guessing.",
  },
];

export function AiFinancialAgentSection() {
  const prefersReducedMotion = useReducedMotion();

  return (
    <section className="relative isolate overflow-hidden bg-[#08080a] py-24 sm:py-32">
      <div className="absolute inset-0" aria-hidden="true">
        <AgentCanvas reducedMotion={prefersReducedMotion} />
      </div>
      <div
        aria-hidden="true"
        className="absolute inset-0 bg-gradient-to-t from-[#08080a] via-[#08080a]/75 to-[#08080a]/25"
      />

      <div className="relative mx-auto max-w-3xl px-5 text-center sm:px-8">
        <p className="text-xs font-semibold tracking-wide text-emerald-200 uppercase">
          Ask questions of the report
        </p>
        <h2 className="mt-4 font-display text-4xl font-semibold text-white sm:text-5xl">
          Every answer traces back to a filing, too.
        </h2>
        <p className="mt-5 text-lg leading-relaxed text-slate-100/90">
          Every report ships with an assistant you can question directly. It
          answers from the same filed data the report is built from, points
          to the exact filing behind every claim, and says it plainly when
          something wasn&rsquo;t disclosed.
        </p>

        <dl className="mt-14 grid grid-cols-1 gap-6 text-left sm:grid-cols-3">
          {CAPABILITIES.map((item) => (
            <div
              key={item.heading}
              className="rounded-2xl border border-white/10 bg-white/5 p-6 backdrop-blur"
            >
              <dt className="text-sm font-semibold text-white">
                {item.heading}
              </dt>
              <dd className="mt-2 text-sm leading-relaxed text-slate-300">
                {item.body}
              </dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  );
}
