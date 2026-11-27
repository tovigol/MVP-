export type TransactionStatus = 'Pending' | 'Completed' | 'Failed';

export interface Transaction {
  transactionId: string;
  amount: number;
  currency: string;
  status: TransactionStatus;
  timestamp: string; // ISO 8601, e.g. 2024-01-15T10:00:00Z
}

export const CURRENCIES = ['USD', 'EUR', 'GBP', 'ILS', 'JPY'] as const;

export function randomTransaction(): Transaction {
  const statuses: TransactionStatus[] = ['Pending', 'Completed', 'Failed'];
  return {
    transactionId: crypto.randomUUID(),
    amount: Number((Math.random() * 9800 + 5).toFixed(2)),
    currency: CURRENCIES[Math.floor(Math.random() * CURRENCIES.length)],
    status: statuses[Math.floor(Math.random() * statuses.length)],
    timestamp: new Date().toISOString()
  };
}
