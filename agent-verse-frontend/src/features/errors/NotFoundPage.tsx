import { Link } from 'react-router-dom';
import { Search, Home, ArrowLeft } from 'lucide-react';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { JARVISStagger } from '@/components/ui/JARVISPageShell';

export default function NotFoundPage() {
  return (
    <JARVISPageShell>

      {/* a11y: live region for async updates */}
      <div aria-live="polite" aria-atomic="true" className="sr-only" />
    <JARVISStagger className="flex flex-col items-center justify-center min-h-[70vh] p-8 text-center">
      <div className="mb-8">
        <p className="text-8xl font-bold text-[#00D4FF]/20 select-none">404</p>
        <Search className="h-16 w-16 text-muted-foreground/30 mx-auto -mt-4" />
      </div>
      <h1 className="text-2xl font-bold mb-2">Page not found</h1>
      <p className="text-muted-foreground mb-8 max-w-md">
        The page you&apos;re looking for doesn&apos;t exist or has been moved.
      </p>
      <div className="flex gap-3">
        <Link
          to="/dashboard"
          className="flex items-center gap-2 px-4 py-2 bg-[#00D4FF] text-[#00D4FF]-foreground rounded-lg hover:opacity-90 transition-opacity"
        >
          <Home className="h-4 w-4" />
          Go to Dashboard
        </Link>
        <button
          onClick={() => window.history.back()}
          className="flex items-center gap-2 px-4 py-2 border border-input rounded-lg hover:bg-muted/50 transition-colors"
        >
          <ArrowLeft className="h-4 w-4" />
          Go Back
        </button>
      </div>
    </JARVISStagger>
    </JARVISPageShell>
  );
}
