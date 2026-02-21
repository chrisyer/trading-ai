import { cn } from '../lib/utils';

export function Card({ className, children, ...props }) {
  return (
    <div
      className={cn(
        'rounded-xl border border-[var(--border)] bg-[var(--bg-card)] p-5',
        className
      )}
      {...props}
    >
      {children}
    </div>
  );
}

export function CardHeader({ className, children }) {
  return (
    <div className={cn('mb-3 flex items-center justify-between', className)}>
      {children}
    </div>
  );
}

export function CardTitle({ className, children }) {
  return (
    <h3 className={cn('text-sm font-semibold uppercase tracking-wider text-[var(--text-secondary)]', className)}>
      {children}
    </h3>
  );
}
