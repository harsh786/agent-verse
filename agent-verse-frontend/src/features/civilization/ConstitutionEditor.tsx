/**
 * Constitution Editor — live editor for civilization governance rules.
 */
import { useState } from 'react';
import type { CivilizationConstitution } from '../../lib/api/civilizationApi';

interface ConstitutionEditorProps {
  constitution: CivilizationConstitution;
  onSave: (constitution: CivilizationConstitution) => Promise<void>;
  readOnly?: boolean;
}

export function ConstitutionEditor({ constitution, onSave, readOnly = false }: ConstitutionEditorProps) {
  const [draft, setDraft] = useState(() => JSON.stringify(constitution, null, 2));
  const [saving, setSaving] = useState(false);
  const [parseError, setParseError] = useState<string | null>(null);

  const handleSave = async () => {
    let parsed: CivilizationConstitution;
    try {
      parsed = JSON.parse(draft) as CivilizationConstitution;
      setParseError(null);
    } catch (e) {
      setParseError((e as Error).message);
      return;
    }
    setSaving(true);
    try {
      await onSave(parsed);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="text-sm font-medium text-gray-700">Constitution</div>
      <textarea
        value={draft}
        onChange={e => setDraft(e.target.value)}
        readOnly={readOnly}
        className="w-full h-64 text-xs font-mono border rounded p-2 bg-gray-50 focus:ring-1 focus:ring-blue-400 resize-none"
        spellCheck={false}
      />
      {parseError && (
        <div className="text-xs text-red-500 bg-red-50 rounded p-2">{parseError}</div>
      )}
      {!readOnly && (
        <button
          onClick={() => void handleSave()}
          disabled={saving}
          className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-40"
        >
          {saving ? 'Saving...' : 'Save Constitution'}
        </button>
      )}
    </div>
  );
}
