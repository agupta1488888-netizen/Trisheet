/**
 * Decorative hero graphic: an abstract, colourful impression of a report —
 * bars, a ring, floating badges. Purely decorative (aria-hidden); it carries
 * no real figures, unlike `ReportPreview`, which does.
 */

export function HeroIllustration() {
  return (
    <div
      aria-hidden="true"
      className="relative mx-auto w-full max-w-md rounded-3xl bg-gradient-to-br from-emerald-500 via-teal-500 to-blue-600 p-8 shadow-2xl shadow-blue-900/30"
    >
      <div className="rounded-2xl bg-white/95 p-6 shadow-lg backdrop-blur">
        <div className="flex items-center justify-between">
          <div className="h-2.5 w-24 rounded-full bg-slate-200" />
          <div className="h-6 w-6 rounded-full bg-emerald-500" />
        </div>

        <div className="mt-6 flex items-end gap-2">
          {[40, 65, 50, 80, 95].map((height, index) => (
            <div
              key={index}
              className="w-full rounded-t-md bg-gradient-to-t from-blue-600 to-emerald-400"
              style={{ height: `${height}px` }}
            />
          ))}
        </div>

        <div className="mt-6 flex items-center gap-4">
          <svg viewBox="0 0 36 36" className="size-16 shrink-0">
            <circle
              cx="18"
              cy="18"
              r="15.5"
              fill="none"
              stroke="#e2e8f0"
              strokeWidth="4"
            />
            <circle
              cx="18"
              cy="18"
              r="15.5"
              fill="none"
              stroke="#10b981"
              strokeWidth="4"
              strokeDasharray="75 100"
              strokeLinecap="round"
              transform="rotate(-90 18 18)"
            />
          </svg>
          <div className="flex flex-col gap-2">
            <div className="h-2 w-32 rounded-full bg-slate-200" />
            <div className="h-2 w-20 rounded-full bg-slate-200" />
          </div>
        </div>
      </div>

      <div className="absolute -top-4 -right-4 flex items-center gap-1.5 rounded-full bg-white px-3 py-1.5 text-xs font-medium text-emerald-700 shadow-lg">
        <span className="size-2 rounded-full bg-emerald-500" />
        Verified
      </div>
      <div className="absolute -bottom-4 -left-4 rounded-full bg-white px-3 py-1.5 text-xs font-medium text-blue-700 shadow-lg">
        100% sourced
      </div>
    </div>
  );
}
