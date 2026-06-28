import type { ReactNode } from 'react';

interface MetaItem {
  label: string;
  value: string;
}

interface Tab {
  key: string;
  label: string;
}

interface DetailLayoutProps {
  title: string;
  subtitle?: string;
  status?: string;
  meta?: MetaItem[];
  actions?: ReactNode;
  tabs: Tab[];
  activeTab: string;
  onTabChange: (tab: string) => void;
  children: ReactNode;
}

const STATUS_COLORS: Record<string, string> = {
  active:   'bg-green-100 text-green-800',
  inactive: 'bg-gray-100 text-gray-600',
  error:    'bg-red-100 text-red-800',
  pending:  'bg-yellow-100 text-yellow-800',
  running:  'bg-blue-100 text-blue-800',
  complete: 'bg-green-100 text-green-800',
  failed:   'bg-red-100 text-red-800',
  pass:     'bg-green-100 text-green-800',
  fail:     'bg-red-100 text-red-800',
};

function InlineStatus({ status }: { status: string }) {
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_COLORS[status] ?? 'bg-muted text-muted-foreground'}`}>
      {status}
    </span>
  );
}

export function DetailLayout({
  title, subtitle, status, meta, actions,
  tabs, activeTab, onTabChange, children,
}: DetailLayoutProps) {
  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="bg-card border-b px-6 py-4">
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-2 flex-wrap">
              <h1 className="text-xl font-semibold">{title}</h1>
              {status && <InlineStatus status={status} />}
            </div>
            {subtitle && <p className="text-sm text-muted-foreground mt-0.5">{subtitle}</p>}
            {meta && meta.length > 0 && (
              <div className="flex flex-wrap gap-4 mt-2">
                {meta.map((m) => (
                  <div key={m.label} className="text-xs">
                    <span className="text-muted-foreground">{m.label}: </span>
                    <span className="font-medium">{m.value}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
          {actions && <div className="flex gap-2 ml-4 flex-shrink-0">{actions}</div>}
        </div>

        {/* Tab bar */}
        <div className="flex gap-1 mt-4 border-b -mb-px">
          {tabs.map((tab) => (
            <button
              key={tab.key}
              role="tab"
              aria-selected={activeTab === tab.key}
              onClick={() => onTabChange(tab.key)}
              className={`px-3 py-2 text-sm font-medium border-b-2 transition-colors ${
                activeTab === tab.key
                  ? 'border-primary text-primary'
                  : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto p-6">{children}</div>
    </div>
  );
}
