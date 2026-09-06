// WebRTC stream-session hookup placeholder.
// Live mode: POST /cad/v1/streams/{id}/session returns signalling details for a
// real RTCPeerConnection. Demo mode: renders a simulated feed frame instead.

import { useCallback, useEffect, useRef, useState } from 'react';

import { isDemoMode, openStreamSession } from './api';
import type { CameraStream, StreamSession } from './types';

export interface StreamSessionState {
  session: StreamSession | null;
  connecting: boolean;
  demo: boolean;
  error: string | null;
}

export function useStreamSession(stream: CameraStream | null) {
  const [state, setState] = useState<StreamSessionState>({
    session: null,
    connecting: false,
    demo: isDemoMode(),
    error: null,
  });
  const pcRef = useRef<RTCPeerConnection | null>(null);

  const connect = useCallback(async () => {
    if (!stream || !stream.online) return;
    setState((s) => ({ ...s, connecting: true, error: null }));
    try {
      const session = await openStreamSession(stream);
      if (!isDemoMode() && typeof RTCPeerConnection !== 'undefined') {
        // Placeholder: a production build would exchange SDP offers against
        // session.offer_url and attach the remote MediaStream to <video>.
        pcRef.current = new RTCPeerConnection({
          iceServers: session.ice_servers.map((urls) => ({ urls })),
        });
      }
      setState({ session, connecting: false, demo: isDemoMode(), error: null });
    } catch (err) {
      setState({
        session: null,
        connecting: false,
        demo: isDemoMode(),
        error: err instanceof Error ? err.message : 'session failed',
      });
    }
  }, [stream]);

  const disconnect = useCallback(() => {
    pcRef.current?.close();
    pcRef.current = null;
    setState({ session: null, connecting: false, demo: isDemoMode(), error: null });
  }, []);

  useEffect(() => disconnect, [disconnect, stream?.stream_id]);

  return { ...state, connect, disconnect };
}
