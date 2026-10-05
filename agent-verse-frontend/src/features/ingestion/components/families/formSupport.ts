import type { SourceFamily } from '../../types';

/** Families whose form renders server field errors next to each field. */
export function formShowsFieldErrors(family: SourceFamily, sourceType: string): boolean {
  return (family === 'nosql_database' && sourceType !== 'redis') || family === 'olap_database' || family === 'oltp_database';
}
