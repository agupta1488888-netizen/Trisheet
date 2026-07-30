import { InputScreen } from "@/components/input/input-screen";

/**
 * The input screen.
 *
 * Everything interactive lives in `InputScreen`; this route is a shell over
 * it, so the same screen can be mounted against fixtures in the preview
 * harness without duplicating a line of it.
 */
export default function Home() {
  return <InputScreen />;
}
