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
}

/** Renders the family-specific config form for a trigger. Shared by the create
 * modal and the detail-drawer edit flow so both stay in lock-step. */
export function FamilyFormRouter({ family, triggerType, value, onChange }: FamilyFormRouterProps) {
  const props = { triggerType, value, onChange };
  switch (family) {
    case 'time':
      return <TimeFamilyForm {...props} />;
    case 'goal_chain':
      return <GoalChainFamilyForm {...props} />;
    case 'webhook':
      return <WebhookFamilyForm {...props} />;
    case 'conversational':
      return <ConversationalFamilyForm {...props} />;
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
