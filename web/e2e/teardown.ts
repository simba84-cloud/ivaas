import { rmSync } from "node:fs";

/** Remove the end-to-end API's object folder (see playwright.config.ts). */
export default function teardown(): void {
  const dir = process.env.IVAAS_E2E_OBJECTS;
  if (dir) rmSync(dir, { recursive: true, force: true });
}
