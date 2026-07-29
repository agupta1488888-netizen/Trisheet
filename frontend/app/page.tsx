import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/**
 * Input screen. Structural shell only — submission is wired in phase 1.
 */
export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center px-6 py-24">
      <h1 className="text-4xl">Tearsheet</h1>
      <p className="mt-2 text-muted-foreground">
        Company profiles, sourced from filings.
      </p>

      <div className="mt-10 border-t border-rule pt-8">
        <label htmlFor="ticker" className="text-sm text-muted-foreground">
          Ticker
        </label>
        <div className="mt-2 flex gap-3">
          <Input
            id="ticker"
            name="ticker"
            placeholder="AAPL"
            autoComplete="off"
            spellCheck={false}
            className="ref uppercase"
            disabled
          />
          <Button disabled>Build profile</Button>
        </div>
        <p className="mt-3 text-sm text-muted-foreground">
          US-listed companies. Every figure traces back to an SEC filing.
        </p>
      </div>
    </main>
  );
}
