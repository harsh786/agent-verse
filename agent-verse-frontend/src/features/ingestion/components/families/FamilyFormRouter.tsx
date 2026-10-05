import type { SourceFamily } from '../../types';
import { ObjectStorageForm } from './ObjectStorageForm';
import { DatabaseForm } from './DatabaseForm';
import { RedisForm } from './RedisForm';
import { ElasticsearchForm } from './ElasticsearchForm';
import { StreamingForm } from './StreamingForm';
import { CommunicationForm } from './CommunicationForm';
import { CodeRepoForm } from './CodeRepoForm';
import { WebForm } from './WebForm';
import { GenericSourceForm } from './GenericSourceForm';

/** The connection form for a source family/type (create wizard and edit drawer). */
export function FamilyFormRouter({ family, sourceType, value, onChange, errors }: {
  family: SourceFamily; sourceType: string;
  value: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void;
  /** connection_config field errors from the server, keyed by config key. */
  errors?: Record<string, string>;
}) {
  switch (family) {
    case 'object_storage': return <ObjectStorageForm sourceType={sourceType} value={value} onChange={onChange} />;
    case 'nosql_database':
      if (sourceType === 'redis') return <RedisForm sourceType={sourceType} value={value} onChange={onChange} />;
      if (sourceType === 'elasticsearch' || sourceType === 'opensearch') return <ElasticsearchForm sourceType={sourceType} value={value} onChange={onChange} />;
      return <DatabaseForm sourceType={sourceType} value={value} onChange={onChange} errors={errors} />;
    case 'olap_database':
    case 'oltp_database': return <DatabaseForm sourceType={sourceType} value={value} onChange={onChange} errors={errors} />;
    case 'streaming':      return <StreamingForm sourceType={sourceType} value={value} onChange={onChange} />;
    case 'communication':  return <CommunicationForm sourceType={sourceType} value={value} onChange={onChange} />;
    case 'code_repository': return <CodeRepoForm sourceType={sourceType} value={value} onChange={onChange} />;
    case 'web':            return <WebForm sourceType={sourceType} value={value} onChange={onChange} />;
    default:               return <GenericSourceForm sourceType={sourceType} value={value} onChange={onChange} />;
  }
}
