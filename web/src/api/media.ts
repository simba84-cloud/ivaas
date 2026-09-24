import { useQuery } from "@tanstack/react-query";

// MediaMTX serves each path as a WebRTC (WHEP) player page on :8889.
export const MEDIA_BASE = import.meta.env.VITE_MEDIA_URL ?? "http://localhost:8889";

/**
 * An iframe cannot report a refused connection, so probe the media server itself.
 * Shared so the camera wall and the dashboard's health strip agree.
 */
export function useMediaServerUp(): boolean | undefined {
  const probe = useQuery({
    queryKey: ["media-server"],
    queryFn: () =>
      fetch(MEDIA_BASE, { mode: "no-cors" }).then(
        () => true,
        () => false,
      ),
    refetchInterval: 30_000,
  });
  return probe.data;
}
