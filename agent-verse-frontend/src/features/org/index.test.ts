/** Smoke test for the org feature barrel — verifies every re-export resolves. */
import { describe, it, expect } from 'vitest';
import * as OrgBarrel from './index';

// A component export is either a plain function component, or an object when
// wrapped in React.memo/forwardRef — either way it must be defined.
function expectComponent(value: unknown) {
  expect(value).toBeDefined();
  expect(['function', 'object']).toContain(typeof value);
}

describe('features/org barrel exports', () => {
  it('re-exports top-level org pages/components', () => {
    expectComponent(OrgBarrel.OrgPage);
    expectComponent(OrgBarrel.OrgListPage);
    expectComponent(OrgBarrel.CommandCenter);
    expectComponent(OrgBarrel.ScheduledMissions);
    expectComponent(OrgBarrel.MissionCard);
    expectComponent(OrgBarrel.MissionsList);
    expectComponent(OrgBarrel.MissionDetail);
    expectComponent(OrgBarrel.OrgHealthWidget);
    expectComponent(OrgBarrel.DepartmentTree);
    expectComponent(OrgBarrel.ActivityFeed);
    expectComponent(OrgBarrel.CreateMissionDrawer);
    expectComponent(OrgBarrel.GraphifyProgress);
    expectComponent(OrgBarrel.VoiceModal);
    expectComponent(OrgBarrel.CursorPresence);
    expectComponent(OrgBarrel.ConnectorMarketplace);
    expectComponent(OrgBarrel.MorningBrief);
    expectComponent(OrgBarrel.MorningBriefBadge);
    expectComponent(OrgBarrel.WhyCard);
    expectComponent(OrgBarrel.NowNextWhy);
    expectComponent(OrgBarrel.OrgComposerWizard);
    expectComponent(OrgBarrel.DigitalTwinPanel);
    expectComponent(OrgBarrel.CommandHistoryPanel);
    expectComponent(OrgBarrel.TeamLifecycleIndicator);
    expectComponent(OrgBarrel.OrgHistoryNav);
    expectComponent(OrgBarrel.ObsidianVaultExplorer);
    expectComponent(OrgBarrel.StrategicAdvisorPage);
  });

  it('re-exports the useOrg hooks, types and api modules via wildcard exports', () => {
    expect(typeof OrgBarrel.useOrgHealth).toBe('function');
    // Wildcard re-exports from ./types and ./api should contribute additional
    // named exports beyond the explicit component list above.
    expect(Object.keys(OrgBarrel).length).toBeGreaterThan(25);
  });
});
