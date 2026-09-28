/**
 * Live platform events, as they arrive on the event socket.
 *
 * `useLiveEvents` owns the one socket and refreshes cached queries; pages that need
 * the events themselves (the command view's activity feed and timeline) subscribe
 * here rather than opening a second connection.
 */
export interface LiveMessage {
  subject: string;
  data: Record<string, unknown>;
  /** when this browser received it, epoch ms: the server does not stamp events */
  at: number;
}

type Listener = (message: LiveMessage) => void;
const listeners = new Set<Listener>();

export function emitLive(message: LiveMessage): void {
  for (const listener of listeners) listener(message);
}

export function onLive(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
