import { Suspense } from "react";

import { InputScreen } from "@/components/input/input-screen";
import { FilingsFeedSection } from "@/components/input/filings-feed-section";

/**
 * The input screen.
 *
 * Everything interactive lives in `InputScreen`; this route is a shell over
 * it, so the same screen can be mounted against fixtures in the preview
 * harness without duplicating a line of it.
 *
 * The feed is rendered here and passed down rather than imported by the screen
 * itself: it is an async server component, and `InputScreen` is a client one,
 * which cannot render one as a child. This route is the nearest server
 * boundary, so it is where the element gets made.
 */
export default function Home() {
  return (
    <InputScreen
      feed={
        // Suspended so the hero is never held behind the backend. The feed
        // sits below the fold and the backend can be cold, so a reader who
        // came here to type a ticker should not wait on a section they have
        // not scrolled to yet.
        <Suspense fallback={null}>
          <FilingsFeedSection />
        </Suspense>
      }
    />
  );
}
