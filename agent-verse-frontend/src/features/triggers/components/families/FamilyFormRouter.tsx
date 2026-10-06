import type { TriggerFamily, TriggerType } from '../../types';
import { TimeFamilyForm } from './TimeFamilyForm';
import { GoalChainFamilyForm } from './GoalChainFamilyForm';
import { WebhookFamilyForm } from './WebhookFamilyForm';
import { ConversationalFamilyForm } from './ConversationalFamilyForm';
import { ConditionFamilyForm } from './ConditionFamilyForm';
import { DataFamilyForm } from './DataFamilyForm';
import { MonitoringFamilyForm } from './MonitoringFamilyForm';
import { IoTFamilyForm } from './IoTFamilyForm';
import { PollingFamilyForm } from './PollingFamilyForm';
import { GenericFamilyForm } from './GenericFamilyForm';

interface FamilyFormRouterProps {
  family: TriggerFamily;
  triggerType: TriggerType;
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
  /** Last server validation message (422), shown inline by forms that own the field. */
  serverError?: string;
}

/** Renders the family-specific config form for a trigger. Shared by the create
 * modal and the detail-drawer edit flow so both stay in lock-step. */
export function FamilyFormRouter({ family, triggerType, value, onChange, serverError }: FamilyFormRouterProps) {
  const form = familyForm(family, { triggerType, value, onChange }, serverError);
  // B1-9: only the conversational form showed the server's refusal; every other
  // family closed nothing and said nothing (e.g. a refused interval, 422).
  if (!serverError || family === 'conversational') return form;
  return (
    <>
      {form}
      <p role="alert" className="mt-3 rounded-md border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-800 dark:bg-red-950/40 dark:text-red-300">
        {serverError}
      </p>
    </>
  );
}

function familyForm(
  family: TriggerFamily,
  props: Pick<FamilyFormRouterProps, 'triggerType' | 'value' | 'onChange'>,
  serverError?: string,
) {
  switch (family) {
    case 'time':
      return <TimeFamilyForm {...props} />;
    case 'goal_chain':
      return <GoalChainFamilyForm {...props} />;
    case 'webhook':
      return <WebhookFamilyForm {...props} />;
    case 'conversational':
      return <ConversationalFamilyForm {...props} fieldError={serverError} />;
    case 'state_condition':
      return <ConditionFamilyForm {...props} />;
    case 'data':
      return <DataFamilyForm {...props} />;
    case 'monitoring':
      return <MonitoringFamilyForm {...props} />;
    case 'iot':
      return <IoTFamilyForm {...props} />;
    case 'ml_signal':
      return <PollingFamilyForm {...props} />;
    default:
      return <GenericFamilyForm {...props} />;
  }
}
