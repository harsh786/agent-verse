/**
 * Pagination — fully accessible page navigation component.
 *
 * Usage:
 *   <Pagination
 *     page={page}
 *     pageSize={pageSize}
 *     total={total}
 *     onPageChange={setPage}
 *     onPageSizeChange={setPageSize}
 *   />
 */
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from "lucide-react";

interface PaginationProps {
  page: number;        // 1-indexed
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  onPageSizeChange?: (size: number) => void;
  pageSizeOptions?: number[];
  className?: string;
}

export function Pagination({
  page,
  pageSize,
  total,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = [10, 25, 50, 100],
  className = "",
}: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);

  // Generate page window: [first, ..., current-1, current, current+1, ..., last]
  const getPageNumbers = (): (number | "ellipsis")[] => {
    if (totalPages <= 7) return Array.from({ length: totalPages }, (_, i) => i + 1);
    const pages: (number | "ellipsis")[] = [1];
    if (page > 3) pages.push("ellipsis");
    for (let i = Math.max(2, page - 1); i <= Math.min(totalPages - 1, page + 1); i++) {
      pages.push(i);
    }
    if (page < totalPages - 2) pages.push("ellipsis");
    pages.push(totalPages);
    return pages;
  };

  const btnBase =
    "inline-flex items-center justify-center h-8 min-w-[2rem] px-2 text-sm rounded-md border border-input transition-colors disabled:opacity-40 disabled:cursor-not-allowed";
  const btnActive = `${btnBase} bg-primary text-primary-foreground border-primary`;
  const btnDefault = `${btnBase} bg-background hover:bg-muted/60`;

  return (
    <nav
      aria-label="Pagination"
      className={`flex flex-wrap items-center justify-between gap-3 text-sm ${className}`}
    >
      <p className="text-muted-foreground text-xs">
        {total === 0 ? "No results" : `${from}–${to} of ${total}`}
      </p>

      <div className="flex items-center gap-1">
        <button
          aria-label="First page"
          onClick={() => onPageChange(1)}
          disabled={page === 1}
          className={btnDefault}
        >
          <ChevronsLeft className="h-3.5 w-3.5" />
        </button>
        <button
          aria-label="Previous page"
          onClick={() => onPageChange(page - 1)}
          disabled={page === 1}
          className={btnDefault}
        >
          <ChevronLeft className="h-3.5 w-3.5" />
        </button>

        {getPageNumbers().map((p, i) =>
          p === "ellipsis" ? (
            <span key={`ellipsis-${i}`} className="px-1 text-muted-foreground select-none">…</span>
          ) : (
            <button
              key={p}
              aria-label={`Page ${p}`}
              aria-current={p === page ? "page" : undefined}
              onClick={() => onPageChange(p as number)}
              className={p === page ? btnActive : btnDefault}
            >
              {p}
            </button>
          )
        )}

        <button
          aria-label="Next page"
          onClick={() => onPageChange(page + 1)}
          disabled={page === totalPages}
          className={btnDefault}
        >
          <ChevronRight className="h-3.5 w-3.5" />
        </button>
        <button
          aria-label="Last page"
          onClick={() => onPageChange(totalPages)}
          disabled={page === totalPages}
          className={btnDefault}
        >
          <ChevronsRight className="h-3.5 w-3.5" />
        </button>
      </div>

      {onPageSizeChange && (
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          Rows
          <select
            value={pageSize}
            onChange={(e) => onPageSizeChange(Number(e.target.value))}
            className="h-7 px-2 border border-input rounded bg-background text-foreground text-xs"
            aria-label="Rows per page"
          >
            {pageSizeOptions.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </label>
      )}
    </nav>
  );
}
