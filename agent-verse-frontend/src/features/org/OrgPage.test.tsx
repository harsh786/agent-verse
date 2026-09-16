/**
 * Unit tests for OrgPage — the AI Organization OS command center.
 *
 * OrgPage composes ~25 child components and half a dozen hooks. To exercise
 * OrgPage's OWN callbacks (togglePanel, handleMissionClick, resize handlers,
 * handleOrgEvent, markBooted, …) rather than re-testing already-covered
 * children, every child component and hook is replaced with a thin stub that
 * exposes its received props as clickable buttons / captured callbacks. This
 * lets each test directly trigger the exact branch of OrgPage logic it wants
 * to prove, independent of any child's own internal behaviour or data needs.
 */
import { render, screen, fireEvent, within, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import React, { type ReactNode } from 'react';

// ─── Hoisted shared mock state ────────────────────────────────────────────────

const mocks = vi.hoisted(() => ({
  reducedMotion: vi.fn(() => true),
  applyEvent: vi.fn(),
  jarvisHandleEvent: vi.fn(),
  toggleJarvisSpeech: vi.fn(),
  jarvisSpeechEnabled: false,
  refetch: vi.fn(),
  capturedOnEvent: { current: null as ((e: unknown) => void) | null },
  useOrganization: vi.fn(),
  useOrgHealth: vi.fn(),
  useMissions: vi.fn(),
  useDepartments: vi.fn(),
}));

function defaultMockState() {
  mocks.reducedMotion.mockReturnValue(true);
  mocks.jarvisSpeechEnabled = false;
  mocks.useOrganization.mockReturnValue({
    data: { name: 'Acme Org', status: 'active' },
    isLoading: false,
    refetch: mocks.refetch,
  });
  mocks.useOrgHealth.mockReturnValue({ data: { pending_approvals: 3 } });
  mocks.useMissions.mockReturnValue({
    data: { pages: [{ data: [
      { id: 'm1', status: 'active', title: 'Mission One' },
      { id: 'm2', status: 'queued', title: 'Mission Two' },
    ] }] },
  });
  mocks.useDepartments.mockReturnValue({
    data: [{ id: 'd1', name: 'Engineering', status: 'active', purpose: 'Build', agent_count: 2 }],
  });
}

// ─── Mocks: framer-motion (avoid animation timing issues) ─────────────────────

vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  const makeStub = (tag: string) =>
    ({ children, ...props }: { children?: ReactNode; [k: string]: unknown }) =>
      React.createElement(tag, props as Record<string, unknown>, children);
  return {
    ...actual,
    useReducedMotion: () => mocks.reducedMotion(),
    AnimatePresence: ({ children }: { children?: ReactNode }) => <>{children}</>,
    motion: new Proxy(actual.motion as unknown as Record<string, unknown>, {
      get: (target, key: string) => (key in target ? target[key] : makeStub(key)),
    }),
  };
});

// ─── Mocks: data hooks ─────────────────────────────────────────────────────────

vi.mock('./hooks/useOrg', () => ({
  useOrganization: (...args: unknown[]) => mocks.useOrganization(...args),
  useOrgHealth: (...args: unknown[]) => mocks.useOrgHealth(...args),
  useMissions: (...args: unknown[]) => mocks.useMissions(...args),
  useDepartments: (...args: unknown[]) => mocks.useDepartments(...args),
}));

vi.mock('./hooks/useOrgNeuralState', () => ({
  useOrgNeuralState: () => ({
    agents: [{ id: 'a1', label: 'Agent One', role: 'researcher', state: 'executing', goalCount: 2 }],
    communicatingPairs: [],
    applyEvent: mocks.applyEvent,
  }),
}));

vi.mock('./OrgRealtimeManager', () => ({
  useOrgRealtimeManager: (_orgId: unknown, opts: { onEvent?: (e: unknown) => void }) => {
    mocks.capturedOnEvent.current = opts?.onEvent ?? null;
  },
}));

vi.mock('@/lib/voice/useVoiceAlerts', () => ({ useVoiceAlerts: () => undefined }));
vi.mock('@/lib/voice/useJarvisSpeech', () => ({
  useJarvisSpeech: () => ({ handleEvent: mocks.jarvisHandleEvent }),
}));
vi.mock('@/stores/voicePrefs', () => ({
  useVoicePrefsStore: (selector: (s: { jarvisSpeechEnabled: boolean; toggleJarvisSpeech: () => void }) => unknown) =>
    selector({ jarvisSpeechEnabled: mocks.jarvisSpeechEnabled, toggleJarvisSpeech: mocks.toggleJarvisSpeech }),
}));

// ─── Mocks: child components (thin stubs exposing props as buttons) ──────────

vi.mock('@/features/knowledge-graph/InteractiveKnowledgeGraph', () => ({
  InteractiveKnowledgeGraph: () => <div data-testid="interactive-knowledge-graph" />,
}));
vi.mock('@/components/ui/JARVISPageShell', () => ({
  JARVISPageShell: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
}));
vi.mock('@/components/ui/JARVISBootScreen', () => ({
  JARVISBootScreen: (p: { onComplete: () => void }) => (
    <div data-testid="boot-screen">
      <button onClick={p.onComplete}>complete-boot</button>
    </div>
  ),
}));
vi.mock('./components/OrgHealthWidget', () => ({
  OrgHealthWidget: () => <div data-testid="org-health-widget" />,
}));
vi.mock('./components/MissionsList', () => ({
  MissionsList: (p: { onMissionClick: (m: unknown) => void; onCreateClick: () => void }) => (
    <div data-testid="missions-list">
      <button onClick={() => p.onMissionClick({ id: 'm1' })}>mission-click</button>
      <button onClick={() => p.onCreateClick()}>list-create-click</button>
    </div>
  ),
}));
vi.mock('./components/MissionDetail', () => ({
  MissionDetail: (p: { onClose: () => void }) => (
    <div data-testid="mission-detail"><button onClick={p.onClose}>close-mission-detail</button></div>
  ),
}));
vi.mock('./components/DepartmentTree', () => ({
  DepartmentTree: (p: { onDeptSelect: (d: { id: string; name: string }) => void }) => (
    <div data-testid="department-tree">
      <button onClick={() => p.onDeptSelect({ id: 'd1', name: 'Engineering' })}>select-dept</button>
    </div>
  ),
}));
vi.mock('./components/ActivityFeed', () => ({
  ActivityFeed: () => <div data-testid="activity-feed" />,
}));
vi.mock('./components/CreateMissionDrawer', () => ({
  CreateMissionDrawer: (p: { open: boolean; onClose: () => void }) =>
    p.open ? <div data-testid="create-mission-drawer"><button onClick={p.onClose}>close-create</button></div> : null,
}));
vi.mock('./components/GraphifyProgress', () => ({
  GraphifyProgress: (p: { onClose: () => void; onComplete: () => void; onViewGraph: () => void }) => (
    <div data-testid="graphify-progress">
      <button onClick={p.onClose}>graphify-close</button>
      <button onClick={p.onComplete}>graphify-complete</button>
      <button onClick={p.onViewGraph}>graphify-view</button>
    </div>
  ),
}));
vi.mock('./components/VoiceModal', () => ({
  VoiceModal: (p: { open: boolean; onClose: () => void; onTranscript: (t: string) => void }) =>
    p.open ? (
      <div data-testid="voice-modal">
        <button onClick={() => p.onTranscript('build me a report')}>voice-transcript</button>
        <button onClick={p.onClose}>voice-close</button>
      </div>
    ) : null,
}));
vi.mock('./components/CursorPresence', () => ({ CursorPresence: () => <div data-testid="cursor-presence" /> }));
vi.mock('./components/ConnectorMarketplace', () => ({
  ConnectorMarketplace: (p: { onClose: () => void }) => (
    <div data-testid="connector-marketplace"><button onClick={p.onClose}>close-connectors</button></div>
  ),
}));
vi.mock('./components/MorningBrief', () => ({ MorningBrief: () => <div data-testid="morning-brief" /> }));
vi.mock('./components/NowNextWhy', () => ({ NowNextWhy: () => <div data-testid="now-next-why" /> }));
vi.mock('./components/OrgHistoryNav', () => ({ OrgHistoryNav: () => <div data-testid="org-history-nav" /> }));
vi.mock('./components/DigitalTwinPanel', () => ({ DigitalTwinPanel: () => <div data-testid="digital-twin-panel" /> }));
vi.mock('./components/CommandHistoryPanel', () => ({ CommandHistoryPanel: () => <div data-testid="command-history-panel" /> }));
vi.mock('./components/MissionSchedules', () => ({ MissionSchedules: () => <div data-testid="mission-schedules" /> }));
vi.mock('./components/ObsidianVaultExplorer', () => ({ ObsidianVaultExplorer: () => <div data-testid="obsidian-vault-explorer" /> }));
vi.mock('./components/MissionOrbit', () => ({ MissionOrbit: () => <div data-testid="mission-orbit" /> }));
vi.mock('./ApprovalCenter', () => ({ ApprovalCenter: () => <div data-testid="approval-center" /> }));
vi.mock('./components/AutonomyControl', () => ({ AutonomyControl: () => <div data-testid="autonomy-control" /> }));
vi.mock('./components/AutonomyStatusBadge', () => ({ AutonomyStatusBadge: () => <div data-testid="autonomy-status-badge" /> }));
vi.mock('./components/NarrationTicker', () => ({ NarrationTicker: () => <div data-testid="narration-ticker" /> }));
vi.mock('./components/BrainFeed', () => ({ BrainFeed: () => <div data-testid="brain-feed" /> }));
vi.mock('./components/BudgetGauges', () => ({ BudgetGauges: () => <div data-testid="budget-gauges" /> }));
vi.mock('./components/TeamChannel', () => ({ TeamChannel: () => <div data-testid="team-channel" /> }));
vi.mock('@/components/voice/LoginGreetingPlayer', () => ({ LoginGreetingPlayer: () => <div data-testid="login-greeting-player" /> }));
vi.mock('./components/AgentConstellation', () => ({
  AgentConstellation: (p: { onAgentSelect: (id: string) => void }) => (
    <div data-testid="agent-constellation"><button onClick={() => p.onAgentSelect('a1')}>select-agent</button></div>
  ),
}));
vi.mock('./components/AgentAuditDrawer', () => ({
  AgentAuditDrawer: (p: { onClose: () => void }) => (
    <div data-testid="agent-audit-drawer"><button onClick={p.onClose}>close-agent-drawer</button></div>
  ),
}));

// ─── Test helpers ──────────────────────────────────────────────────────────────

function renderOrgPage(path = '/org/org-1') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/org/:orgId" element={<OrgPageLazy />} />
          <Route path="/org/:orgId/mission/:missionId" element={<div>Mission Detail Route</div>} />
          <Route path="/knowledge-graph" element={<div>Full Knowledge Graph Route</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

let OrgPageLazy: React.ComponentType;

beforeEach(async () => {
  vi.clearAllMocks();
  defaultMockState();
  window.sessionStorage.clear();
  window.localStorage.clear();
  ({ OrgPage: OrgPageLazy } = await import('./OrgPage'));
});

// ─── Tests ──────────────────────────────────────────────────────────────────────

describe('OrgPage — no organization selected', () => {
  it('renders a placeholder when orgId is missing', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <OrgPageLazy />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getByText('No organization selected.')).toBeInTheDocument();
  });
});

describe('OrgPage — boot sequence', () => {
  it('shows the JARVIS boot screen when reduced motion is off and not yet booted, then boots', () => {
    mocks.reducedMotion.mockReturnValue(false);
    renderOrgPage();
    expect(screen.getByTestId('boot-screen')).toBeInTheDocument();
    fireEvent.click(screen.getByText('complete-boot'));
    expect(screen.queryByTestId('boot-screen')).not.toBeInTheDocument();
    expect(screen.getByRole('banner')).toBeInTheDocument();
    expect(window.sessionStorage.getItem('av_org_booted')).toBe('1');
  });

  it('skips the boot screen when reduced motion is preferred', () => {
    mocks.reducedMotion.mockReturnValue(true);
    renderOrgPage();
    expect(screen.queryByTestId('boot-screen')).not.toBeInTheDocument();
    expect(screen.getByRole('banner')).toBeInTheDocument();
  });

  it('skips the boot screen when already booted this session', () => {
    mocks.reducedMotion.mockReturnValue(false);
    window.sessionStorage.setItem('av_org_booted', '1');
    renderOrgPage();
    expect(screen.queryByTestId('boot-screen')).not.toBeInTheDocument();
  });

  it('falls back to un-booted state when sessionStorage read throws', () => {
    mocks.reducedMotion.mockReturnValue(false);
    const spy = vi.spyOn(window.sessionStorage.__proto__, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    renderOrgPage();
    expect(screen.getByTestId('boot-screen')).toBeInTheDocument();
    spy.mockRestore();
  });
});

describe('OrgPage — header content and controls', () => {
  it('renders the organization name and status', () => {
    renderOrgPage();
    expect(screen.getByText('Acme Org')).toBeInTheDocument();
    expect(screen.getByText('active')).toBeInTheDocument();
  });

  it('shows "Loading…" while the organization query is pending', () => {
    mocks.useOrganization.mockReturnValue({ data: undefined, isLoading: true, refetch: mocks.refetch });
    renderOrgPage();
    expect(screen.getByText('Loading…')).toBeInTheDocument();
  });

  it('calls refetch when the refresh button is clicked', () => {
    renderOrgPage();
    fireEvent.click(screen.getByRole('button', { name: 'Refresh organization data' }));
    expect(mocks.refetch).toHaveBeenCalledTimes(1);
  });

  it('opens the voice modal, submits a transcript, and closes the modal + opens create drawer', () => {
    renderOrgPage();
    fireEvent.click(screen.getByRole('button', { name: 'Voice input' }));
    expect(screen.getByTestId('voice-modal')).toBeInTheDocument();
    fireEvent.click(screen.getByText('voice-transcript'));
    expect(screen.queryByTestId('voice-modal')).not.toBeInTheDocument();
    expect(screen.getByTestId('create-mission-drawer')).toBeInTheDocument();
  });

  it('closes the voice modal via its own close callback', () => {
    renderOrgPage();
    fireEvent.click(screen.getByRole('button', { name: 'Voice input' }));
    fireEvent.click(screen.getByText('voice-close'));
    expect(screen.queryByTestId('voice-modal')).not.toBeInTheDocument();
  });

  it('toggles JARVIS speech narration', () => {
    renderOrgPage();
    fireEvent.click(screen.getByRole('button', { name: /JARVIS voice narration/ }));
    expect(mocks.toggleJarvisSpeech).toHaveBeenCalledTimes(1);
  });

  it('reflects jarvisSpeechEnabled=true in the mute button state', () => {
    mocks.jarvisSpeechEnabled = true;
    renderOrgPage();
    const btn = screen.getByRole('button', { name: 'Mute JARVIS voice narration' });
    expect(btn).toHaveAttribute('aria-pressed', 'true');
  });

  it('opens the create-mission drawer from the New Mission CTA', () => {
    renderOrgPage();
    fireEvent.click(screen.getByRole('button', { name: 'Create new mission' }));
    expect(screen.getByTestId('create-mission-drawer')).toBeInTheDocument();
    fireEvent.click(screen.getByText('close-create'));
    expect(screen.queryByTestId('create-mission-drawer')).not.toBeInTheDocument();
  });

  it('opens the create-mission drawer via the missions list onCreateClick', () => {
    renderOrgPage();
    fireEvent.click(screen.getByText('list-create-click'));
    expect(screen.getByTestId('create-mission-drawer')).toBeInTheDocument();
  });
});

describe('OrgPage — approvals badge', () => {
  it('shows the pending approval count', () => {
    renderOrgPage();
    const btn = screen.getByRole('button', { name: 'Approvals (3 pending)' });
    expect(btn).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
  });

  it('caps the badge at "9+" for large counts', () => {
    mocks.useOrgHealth.mockReturnValue({ data: { pending_approvals: 15 } });
    renderOrgPage();
    expect(screen.getByText('9+')).toBeInTheDocument();
  });

  it('shows no badge and a plain label when there are no pending approvals', () => {
    mocks.useOrgHealth.mockReturnValue({ data: { pending_approvals: 0 } });
    renderOrgPage();
    expect(screen.getByRole('button', { name: 'Approvals' })).toBeInTheDocument();
  });
});

describe('OrgPage — toolbar side panels', () => {
  const cases: Array<[string, string, string]> = [
    ['Open knowledge graph builder', 'Knowledge Graph', 'graphify-progress'],
    ['Connector marketplace', 'Connectors', 'connector-marketplace'],
    ['Digital Twin capacity view', 'Digital Twin', 'digital-twin-panel'],
    ['Command gateway history', 'Command History', 'command-history-panel'],
    ['Organisation history', 'Org History', 'org-history-nav'],
    ['Scheduled missions', 'Scheduled Missions', 'mission-schedules'],
    ['Obsidian vault explorer', 'Knowledge Vault', 'obsidian-vault-explorer'],
  ];

  it.each(cases)('opens and closes the %s panel', (buttonLabel, panelTitle, testId) => {
    renderOrgPage();
    fireEvent.click(screen.getByRole('button', { name: buttonLabel }));
    const dialog = screen.getByRole('dialog', { name: panelTitle });
    expect(within(dialog).getByTestId(testId)).toBeInTheDocument();
    // Close via the panel's own X button.
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close panel' }));
    expect(screen.queryByRole('dialog', { name: panelTitle })).not.toBeInTheDocument();
  });

  it('opens the approvals panel via the toolbar', () => {
    renderOrgPage();
    fireEvent.click(screen.getByRole('button', { name: /^Approvals/ }));
    const dialog = screen.getByRole('dialog', { name: 'Approvals' });
    expect(within(dialog).getByTestId('approval-center')).toBeInTheDocument();
  });

  it('toggles a panel closed when its button is clicked twice', () => {
    renderOrgPage();
    const btn = screen.getByRole('button', { name: 'Command gateway history' });
    fireEvent.click(btn);
    expect(screen.getByRole('dialog', { name: 'Command History' })).toBeInTheDocument();
    fireEvent.click(btn);
    expect(screen.queryByRole('dialog', { name: 'Command History' })).not.toBeInTheDocument();
  });

  it('switches directly from one panel to another', () => {
    renderOrgPage();
    fireEvent.click(screen.getByRole('button', { name: 'Digital Twin capacity view' }));
    expect(screen.getByRole('dialog', { name: 'Digital Twin' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Command gateway history' }));
    expect(screen.queryByRole('dialog', { name: 'Digital Twin' })).not.toBeInTheDocument();
    expect(screen.getByRole('dialog', { name: 'Command History' })).toBeInTheDocument();
  });

  it('closes the active panel by clicking the backdrop', () => {
    renderOrgPage();
    fireEvent.click(screen.getByRole('button', { name: 'Command gateway history' }));
    const dialog = screen.getByRole('dialog', { name: 'Command History' });
    const backdrop = dialog.parentElement?.querySelector('[aria-hidden]');
    expect(backdrop).toBeTruthy();
    fireEvent.click(backdrop as Element);
    expect(screen.queryByRole('dialog', { name: 'Command History' })).not.toBeInTheDocument();
  });

  it('closes the connectors panel via its own onClose callback', () => {
    renderOrgPage();
    fireEvent.click(screen.getByRole('button', { name: 'Connector marketplace' }));
    fireEvent.click(screen.getByText('close-connectors'));
    expect(screen.queryByTestId('connector-marketplace')).not.toBeInTheDocument();
  });
});

describe('OrgPage — Graphify → Knowledge Graph overlay', () => {
  it('onComplete closes the panel and opens the immersive graph overlay', () => {
    renderOrgPage();
    fireEvent.click(screen.getByRole('button', { name: 'Open knowledge graph builder' }));
    fireEvent.click(screen.getByText('graphify-complete'));
    expect(screen.queryByRole('dialog', { name: 'Knowledge Graph' })).not.toBeInTheDocument();
    const overlay = screen.getByRole('dialog', { name: 'Knowledge graph' });
    expect(within(overlay).getByTestId('interactive-knowledge-graph')).toBeInTheDocument();
  });

  it('onViewGraph opens the overlay directly', () => {
    renderOrgPage();
    fireEvent.click(screen.getByRole('button', { name: 'Open knowledge graph builder' }));
    fireEvent.click(screen.getByText('graphify-view'));
    expect(screen.getByRole('dialog', { name: 'Knowledge graph' })).toBeInTheDocument();
  });

  it('closes the overlay via its own close button', () => {
    renderOrgPage();
    fireEvent.click(screen.getByRole('button', { name: 'Open knowledge graph builder' }));
    fireEvent.click(screen.getByText('graphify-view'));
    fireEvent.click(screen.getByRole('button', { name: 'Close knowledge graph' }));
    expect(screen.queryByRole('dialog', { name: 'Knowledge graph' })).not.toBeInTheDocument();
  });

  it('navigates to the full explorer route', () => {
    renderOrgPage();
    fireEvent.click(screen.getByRole('button', { name: 'Open knowledge graph builder' }));
    fireEvent.click(screen.getByText('graphify-view'));
    fireEvent.click(screen.getByRole('button', { name: 'Full explorer' }));
    expect(screen.getByText('Full Knowledge Graph Route')).toBeInTheDocument();
  });
});

describe('OrgPage — missions and department filter', () => {
  it('navigates to the mission detail route when a mission is clicked', () => {
    renderOrgPage();
    fireEvent.click(screen.getByText('mission-click'));
    expect(screen.getByText('Mission Detail Route')).toBeInTheDocument();
  });

  it('selects a department filter and shows a dismissible chip', () => {
    renderOrgPage();
    fireEvent.click(screen.getByText('select-dept'));
    const chip = screen.getByRole('button', { name: 'Clear department filter: Engineering' });
    expect(chip).toBeInTheDocument();
    fireEvent.click(chip);
    expect(screen.queryByRole('button', { name: /Clear department filter/ })).not.toBeInTheDocument();
  });

  it('changes the status filter tab selection', () => {
    renderOrgPage();
    const activeTab = screen.getByRole('tab', { name: 'Active' });
    fireEvent.click(activeTab);
    expect(activeTab).toHaveAttribute('aria-selected', 'true');
    const allTab = screen.getByRole('tab', { name: 'All' });
    expect(allTab).toHaveAttribute('aria-selected', 'false');
  });

  it('renders the mission orbit hero when there are active missions', () => {
    renderOrgPage();
    expect(screen.getByTestId('mission-orbit')).toBeInTheDocument();
    expect(screen.getByText('1 Active Mission')).toBeInTheDocument();
  });

  it('omits the mission orbit hero when there are no active missions', () => {
    mocks.useMissions.mockReturnValue({ data: { pages: [{ data: [{ id: 'm2', status: 'queued' }] }] } });
    renderOrgPage();
    expect(screen.queryByTestId('mission-orbit')).not.toBeInTheDocument();
  });
});

describe('OrgPage — live agent network', () => {
  it('shows the live agent count when neural agents are present', () => {
    renderOrgPage();
    expect(screen.getByText(/1 active/)).toBeInTheDocument();
  });

  it('selects an agent to open the audit drawer, then closes it', () => {
    renderOrgPage();
    fireEvent.click(screen.getByText('select-agent'));
    expect(screen.getByTestId('agent-audit-drawer')).toBeInTheDocument();
    fireEvent.click(screen.getByText('close-agent-drawer'));
    expect(screen.queryByTestId('agent-audit-drawer')).not.toBeInTheDocument();
  });

  it('hides and shows the agent network section', () => {
    renderOrgPage();
    const toggle = screen.getByRole('button', { name: 'Hide agent network' });
    fireEvent.click(toggle);
    expect(screen.queryByTestId('agent-constellation')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show agent network' }));
    expect(screen.getByTestId('agent-constellation')).toBeInTheDocument();
  });
});

describe('OrgPage — org realtime event routing', () => {
  it('routes an incoming org event to both the neural state and JARVIS speech handlers', () => {
    renderOrgPage();
    expect(mocks.capturedOnEvent.current).toBeTypeOf('function');
    mocks.capturedOnEvent.current?.({ type: 'mission_started' });
    expect(mocks.applyEvent).toHaveBeenCalledWith({ type: 'mission_started' });
    expect(mocks.jarvisHandleEvent).toHaveBeenCalledWith({ type: 'mission_started' });
  });
});

describe('OrgPage — resizable panels', () => {
  it('resizes the command panel width via pointer drag and persists it', () => {
    renderOrgPage();
    const separator = screen.getByRole('separator', { name: 'Resize command panel' });
    act(() => {
      const down = new Event('pointerdown', { bubbles: true }) as unknown as PointerEvent;
      Object.defineProperty(down, 'clientX', { value: 500 });
      separator.dispatchEvent(down);
    });
    act(() => {
      const move = new Event('pointermove') as unknown as PointerEvent;
      Object.defineProperty(move, 'clientX', { value: 450 });
      window.dispatchEvent(move);
      window.dispatchEvent(new Event('pointerup'));
    });
    expect(window.localStorage.getItem('av-org-right-w')).toBe('450');
  });

  it('resizes the missions hero section height via pointer drag', () => {
    renderOrgPage();
    const separator = screen.getByRole('separator', { name: 'Resize missions section' });
    act(() => {
      const down = new Event('pointerdown', { bubbles: true }) as unknown as PointerEvent;
      Object.defineProperty(down, 'clientY', { value: 100 });
      separator.dispatchEvent(down);
    });
    act(() => {
      const move = new Event('pointermove') as unknown as PointerEvent;
      Object.defineProperty(move, 'clientY', { value: 150 });
      window.dispatchEvent(move);
      window.dispatchEvent(new Event('pointerup'));
    });
    expect(window.localStorage.getItem('av-org-mission-h')).toBe('550');
  });

  it('resizes the agent network section height via pointer drag', () => {
    renderOrgPage();
    const separator = screen.getByRole('separator', { name: 'Resize agent network section' });
    act(() => {
      const down = new Event('pointerdown', { bubbles: true }) as unknown as PointerEvent;
      Object.defineProperty(down, 'clientY', { value: 100 });
      separator.dispatchEvent(down);
    });
    act(() => {
      const move = new Event('pointermove') as unknown as PointerEvent;
      Object.defineProperty(move, 'clientY', { value: 80 });
      window.dispatchEvent(move);
      window.dispatchEvent(new Event('pointerup'));
    });
    expect(window.localStorage.getItem('av-org-net-h')).toBe('400');
  });

  it('reads a previously persisted width/height from localStorage', () => {
    window.localStorage.setItem('av-org-right-w', '500');
    window.localStorage.setItem('av-org-mission-h', '600');
    window.localStorage.setItem('av-org-net-h', '300');
    renderOrgPage();
    const aside = screen.getByLabelText('Command panel');
    expect(aside).toHaveStyle({ width: '500px' });
  });

  it('falls back to defaults when localStorage read throws', () => {
    const spy = vi.spyOn(window.localStorage, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    renderOrgPage();
    const aside = screen.getByLabelText('Command panel');
    expect(aside).toHaveStyle({ width: '400px' });
    spy.mockRestore();
  });
});
