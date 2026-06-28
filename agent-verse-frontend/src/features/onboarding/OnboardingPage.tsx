import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { CheckCircle, ChevronRight, Loader2, Zap } from "lucide-react";
import { settingsApi, connectorsApi, goalsApi, agentsApi } from "@/lib/api/client";

type Step = 1 | 2 | 3 | 4;

const STEPS = [
  { id: 1 as Step, label: "Configure LLM" },
  { id: 2 as Step, label: "Add Connector" },
  { id: 3 as Step, label: "Create Agent" },
  { id: 4 as Step, label: "Run First Goal" },
];

// ── Step 1: Configure LLM ─────────────────────────────────────────────────────

function Step1LLM({ onNext }: { onNext: () => void }) {
  const [provider, setProvider] = useState("openai");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("gpt-4o");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  const handleSave = async () => {
    if (!apiKey.trim()) { setError("API key is required"); return; }
    setSaving(true);
    setError("");
    try {
      await settingsApi.setLLM({ provider, api_key: apiKey, default_model: model });
      setSaved(true);
      setTimeout(onNext, 800);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold">Configure your LLM provider</h2>
        <p className="text-muted-foreground text-sm mt-1">
          AgentVerse needs an LLM to power your agents. Add your API key below.
        </p>
      </div>

      <div className="space-y-4">
        <div>
          <label className="block text-sm font-medium mb-1">Provider</label>
          <select
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background focus:ring-2 focus:ring-primary outline-none"
          >
            <option value="openai">OpenAI</option>
            <option value="anthropic">Anthropic</option>
            <option value="google">Google (Gemini)</option>
            <option value="azure_openai">Azure OpenAI</option>
          </select>
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">API Key</label>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={`Your ${provider} API key`}
            className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background focus:ring-2 focus:ring-primary outline-none"
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">Default Model</label>
          <input
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="gpt-4o"
            className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background focus:ring-2 focus:ring-primary outline-none"
          />
        </div>
      </div>

      {error && <p className="text-sm text-red-600">{error}</p>}
      {saved && (
        <p className="text-sm text-green-600 flex items-center gap-1">
          <CheckCircle className="h-4 w-4" /> LLM configured!
        </p>
      )}

      <button
        onClick={handleSave}
        disabled={saving || saved}
        className="flex items-center gap-2 px-5 py-2.5 bg-primary text-primary-foreground text-sm rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity"
      >
        {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
        {saved ? "Saved!" : "Save & Continue"}
        {!saving && !saved && <ChevronRight className="h-4 w-4" />}
      </button>
    </div>
  );
}

// ── Step 2: Register Connector ────────────────────────────────────────────────

function Step2Connector({ onNext, onSkip }: { onNext: () => void; onSkip: () => void }) {
  const [name, setName] = useState("GitHub");
  const [url, setUrl] = useState("https://api.github.com/mcp");
  const [authType, setAuthType] = useState("bearer");
  const [token, setToken] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  const handleRegister = async () => {
    setSaving(true);
    setError("");
    try {
      await connectorsApi.register({
        name,
        url,
        auth_type: authType,
        auth_config: token ? { token } : {},
      });
      setSaved(true);
      setTimeout(onNext, 800);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold">Register your first connector</h2>
        <p className="text-muted-foreground text-sm mt-1">
          Connectors give your agents tools to interact with the world. You can skip this and add connectors later.
        </p>
      </div>

      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium mb-1">Connector Name</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="GitHub"
              className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background focus:ring-2 focus:ring-primary outline-none"
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Auth Type</label>
            <select
              value={authType}
              onChange={(e) => setAuthType(e.target.value)}
              className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background focus:ring-2 focus:ring-primary outline-none"
            >
              <option value="bearer">Bearer Token</option>
              <option value="api_key">API Key</option>
              <option value="none">None</option>
            </select>
          </div>
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">MCP Server URL</label>
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://..."
            className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background focus:ring-2 focus:ring-primary outline-none font-mono"
          />
        </div>
        {authType !== "none" && (
          <div>
            <label className="block text-sm font-medium mb-1">Token / Key</label>
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="Your credentials"
              className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background focus:ring-2 focus:ring-primary outline-none"
            />
          </div>
        )}
      </div>

      {error && <p className="text-sm text-red-600">{error}</p>}
      {saved && (
        <p className="text-sm text-green-600 flex items-center gap-1">
          <CheckCircle className="h-4 w-4" /> Connector registered!
        </p>
      )}

      <div className="flex gap-3">
        <button
          onClick={handleRegister}
          disabled={saving || saved}
          className="flex items-center gap-2 px-5 py-2.5 bg-primary text-primary-foreground text-sm rounded-lg hover:opacity-90 disabled:opacity-50"
        >
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          {saved ? "Registered!" : "Register & Continue"}
          {!saving && !saved && <ChevronRight className="h-4 w-4" />}
        </button>
        <button
          onClick={onSkip}
          className="px-4 py-2.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          Skip for now
        </button>
      </div>
    </div>
  );
}

// ── Step 3: Create Agent ──────────────────────────────────────────────────────

function Step3Agent({
  onNext,
  onAgentCreated,
}: {
  onNext: () => void;
  onAgentCreated: (id: string) => void;
}) {
  const [command, setCommand] = useState("A helpful assistant for code review");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  const handleCreate = async () => {
    if (!command.trim()) return;
    setSaving(true);
    setError("");
    try {
      const data = await agentsApi.createNl(command, false);
      onAgentCreated(data.agent_id ?? '');
      setSaved(true);
      setTimeout(onNext, 800);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold">Create your first agent</h2>
        <p className="text-muted-foreground text-sm mt-1">
          Describe what your agent should do in plain English.
        </p>
      </div>

      <div>
        <label className="block text-sm font-medium mb-1">Agent description</label>
        <textarea
          value={command}
          onChange={(e) => setCommand(e.target.value)}
          rows={3}
          placeholder="A helpful assistant that reviews PRs and reports issues…"
          className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background focus:ring-2 focus:ring-primary outline-none resize-none"
        />
      </div>

      {error && <p className="text-sm text-red-600">{error}</p>}
      {saved && (
        <p className="text-sm text-green-600 flex items-center gap-1">
          <CheckCircle className="h-4 w-4" /> Agent created!
        </p>
      )}

      <button
        onClick={handleCreate}
        disabled={saving || saved || !command.trim()}
        className="flex items-center gap-2 px-5 py-2.5 bg-primary text-primary-foreground text-sm rounded-lg hover:opacity-90 disabled:opacity-50"
      >
        {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
        {saved ? "Created!" : "Create Agent"}
        {!saving && !saved && <ChevronRight className="h-4 w-4" />}
      </button>
    </div>
  );
}

// ── Step 4: Run First Goal ────────────────────────────────────────────────────

function Step4Goal({
  agentId,
  onFinish,
}: {
  agentId: string;
  onFinish: () => void;
}) {
  const [goal, setGoal] = useState("Say hello and list your available tools");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [goalId, setGoalId] = useState("");

  const handleSubmit = async () => {
    if (!goal.trim()) return;
    setSubmitting(true);
    setError("");
    try {
      const res = await goalsApi.submit({
        goal,
        agent_id: agentId || undefined,
      });
      setGoalId(res.goal_id ?? res.id ?? "");
      setSubmitted(true);
    } catch (e) {
      setError(String(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold">Run your first goal</h2>
        <p className="text-muted-foreground text-sm mt-1">
          Submit a goal to see your agent in action. You can monitor progress on the Goals page.
        </p>
      </div>

      {!submitted ? (
        <>
          <div>
            <label className="block text-sm font-medium mb-1">Goal</label>
            <textarea
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              rows={3}
              className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background focus:ring-2 focus:ring-primary outline-none resize-none"
            />
          </div>

          {error && <p className="text-sm text-red-600">{error}</p>}

          <button
            onClick={handleSubmit}
            disabled={submitting || !goal.trim()}
            className="flex items-center gap-2 px-5 py-2.5 bg-primary text-primary-foreground text-sm rounded-lg hover:opacity-90 disabled:opacity-50"
          >
            {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Zap className="h-4 w-4" />}
            Run Goal
          </button>
        </>
      ) : (
        <div className="bg-green-50 border border-green-200 rounded-xl p-5 space-y-3">
          <p className="text-green-700 font-semibold flex items-center gap-2">
            <CheckCircle className="h-5 w-5" /> Goal submitted!
          </p>
          {goalId && (
            <p className="text-xs text-green-600 font-mono">Goal ID: {goalId}</p>
          )}
          <p className="text-sm text-green-700">
            Your agent is now working on it. You can track progress in real time.
          </p>
          <div className="flex gap-3">
            <button
              onClick={onFinish}
              className="px-4 py-2 bg-green-600 text-white text-sm rounded-lg hover:bg-green-700"
            >
              Go to Dashboard
            </button>
            {goalId && (
              <button
                onClick={() => window.location.assign(`/goals/${goalId}`)}
                className="px-4 py-2 border border-green-300 text-green-700 text-sm rounded-lg hover:bg-green-50"
              >
                Watch Goal
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Main OnboardingPage ────────────────────────────────────────────────────────

export function OnboardingPage() {
  const navigate = useNavigate();
  const [currentStep, setCurrentStep] = useState<Step>(1);
  const [createdAgentId, setCreatedAgentId] = useState("");

  const advance = () =>
    setCurrentStep((s) => Math.min(s + 1, 4) as Step);

  return (
    <div className="min-h-screen bg-background flex flex-col items-center justify-center px-4 py-12">
      <div className="w-full max-w-lg">
        {/* Logo */}
        <div className="flex items-center gap-2 justify-center mb-8">
          <Zap className="h-8 w-8 text-blue-500" />
          <span className="text-2xl font-bold">AgentVerse</span>
        </div>

        {/* Progress bar */}
        <div className="flex items-center gap-2 mb-8">
          {STEPS.map((step, i) => (
            <div key={step.id} className="flex items-center gap-2 flex-1">
              <div className="flex flex-col items-center gap-1">
                <div
                  className={`flex items-center justify-center w-8 h-8 rounded-full text-sm font-semibold transition-colors ${
                    step.id < currentStep
                      ? "bg-green-500 text-white"
                      : step.id === currentStep
                      ? "bg-primary text-primary-foreground"
                      : "bg-muted text-muted-foreground"
                  }`}
                >
                  {step.id < currentStep ? <CheckCircle className="h-4 w-4" /> : step.id}
                </div>
                <span className="text-xs text-muted-foreground whitespace-nowrap hidden sm:block">
                  {step.label}
                </span>
              </div>
              {i < STEPS.length - 1 && (
                <div
                  className={`h-px flex-1 transition-colors ${
                    step.id < currentStep ? "bg-green-500" : "bg-border"
                  }`}
                />
              )}
            </div>
          ))}
        </div>

        {/* Step content */}
        <div className="bg-card border border-border rounded-2xl p-6 shadow-sm">
          {currentStep === 1 && <Step1LLM onNext={advance} />}
          {currentStep === 2 && (
            <Step2Connector onNext={advance} onSkip={advance} />
          )}
          {currentStep === 3 && (
            <Step3Agent
              onNext={advance}
              onAgentCreated={(id) => setCreatedAgentId(id)}
            />
          )}
          {currentStep === 4 && (
            <Step4Goal
              agentId={createdAgentId}
              onFinish={() => navigate("/dashboard")}
            />
          )}
        </div>

        {/* Skip all */}
        <div className="text-center mt-4">
          <button
            onClick={() => navigate("/dashboard")}
            className="text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            Skip setup and go to dashboard
          </button>
        </div>
      </div>
    </div>
  );
}
