import { create } from 'zustand';
import type { Transaction, TransactionStatus } from '../types/transaction';

export type ConnectionState = 'connecting' | 'connected' | 'reconnecting' | 'disconnected';

export type StatusFilter = 'All' | TransactionStatus;

const MAX_BUFFERED_TRANSACTIONS = 2000;

interface TransactionState {
  transactions: Transaction[];
  connectionState: ConnectionState;
  statusFilter: StatusFilter;
  lastReceivedId: string | null;

  addTransaction: (tx: Transaction) => void;
  setConnectionState: (state: ConnectionState) => void;
  setStatusFilter: (filter: StatusFilter) => void;
}

export const useTransactionStore = create<TransactionState>((set) => ({
  transactions: [],
  connectionState: 'connecting',
  statusFilter: 'All',
  lastReceivedId: null,

  addTransaction: (tx) =>
    set((state) => {
      // Newest first; cap the buffer so a long-running session doesn't grow unbounded.
      const next = [tx, ...state.transactions];
      if (next.length > MAX_BUFFERED_TRANSACTIONS) {
        next.length = MAX_BUFFERED_TRANSACTIONS;
      }
      return { transactions: next, lastReceivedId: tx.transactionId };
    }),

  setConnectionState: (connectionState) => set({ connectionState }),
  setStatusFilter: (statusFilter) => set({ statusFilter })
}));

export function selectFilteredTransactions(state: TransactionState): Transaction[] {
  if (state.statusFilter === 'All') return state.transactions;
  return state.transactions.filter((t) => t.status === state.statusFilter);
}
