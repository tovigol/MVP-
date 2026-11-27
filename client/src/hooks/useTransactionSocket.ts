import { useEffect, useRef } from 'react';
import * as signalR from '@microsoft/signalr';
import { useTransactionStore } from '../store/transactionStore';
import type { Transaction } from '../types/transaction';

const HUB_URL = import.meta.env.VITE_HUB_URL ?? '/hubs/transactions';

/**
 * Owns the SignalR connection lifecycle for the live dashboard.
 * Reconnects automatically; connection status is mirrored into the store
 * so the UI can show a live/degraded indicator regardless of which
 * pod the backend routed us to (Redis backplane makes that pod-agnostic).
 */
export function useTransactionSocket() {
  const addTransaction = useTransactionStore((s) => s.addTransaction);
  const setConnectionState = useTransactionStore((s) => s.setConnectionState);
  const connectionRef = useRef<signalR.HubConnection | null>(null);

  useEffect(() => {
    const connection = new signalR.HubConnectionBuilder()
      .withUrl(HUB_URL)
      .withAutomaticReconnect([0, 1000, 3000, 5000, 10000])
      .configureLogging(signalR.LogLevel.Warning)
      .build();

    connection.on('TransactionReceived', (tx: Transaction) => {
      addTransaction(tx);
    });

    connection.onreconnecting(() => setConnectionState('reconnecting'));
    connection.onreconnected(() => setConnectionState('connected'));
    connection.onclose(() => setConnectionState('disconnected'));

    setConnectionState('connecting');
    connection
      .start()
      .then(() => setConnectionState('connected'))
      .catch(() => setConnectionState('disconnected'));

    connectionRef.current = connection;

    return () => {
      connection.stop();
    };
  }, [addTransaction, setConnectionState]);
}
