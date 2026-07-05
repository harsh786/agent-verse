import React from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, RefreshCw, Home } from 'lucide-react';

interface State {
  hasError: boolean;
  error: Error | null;
  errorInfo: React.ErrorInfo | null;
}

export class RouteErrorBoundary extends React.Component<
  { children: React.ReactNode; routeName?: string },
  State
> {
  state: State = { hasError: false, error: null, errorInfo: null };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    this.setState({ errorInfo });
    // In production, send to error tracking (e.g. Sentry):
    console.error(`[RouteErrorBoundary:${this.props.routeName ?? 'unknown'}]`, error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex flex-col items-center justify-center min-h-[60vh] p-8 text-center">
          <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-xl p-8 max-w-md w-full">
            <AlertTriangle className="h-10 w-10 text-red-500 mx-auto mb-4" />
            <h2 className="text-lg font-semibold mb-2">Something went wrong</h2>
            {this.props.routeName && (
              <p className="text-sm text-muted-foreground mb-1">
                Error in: <span className="font-medium">{this.props.routeName}</span>
              </p>
            )}
            <p className="text-xs text-red-600 dark:text-red-400 font-mono bg-red-100 dark:bg-red-900/40 rounded p-2 mb-4 text-left overflow-auto max-h-24">
              {this.state.error?.message ?? 'Unknown error'}
            </p>
            <div className="flex gap-3 justify-center">
              <button
                onClick={() => this.setState({ hasError: false, error: null, errorInfo: null })}
                className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm hover:opacity-90"
              >
                <RefreshCw className="h-4 w-4" />
                Try Again
              </button>
              <Link
                to="/"
                className="flex items-center gap-2 px-4 py-2 border border-input rounded-lg text-sm hover:bg-muted/50"
              >
                <Home className="h-4 w-4" />
                Go Home
              </Link>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
