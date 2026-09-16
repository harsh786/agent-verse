import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Code2, Loader2, Zap, Layout, Server, Database, Globe, Smartphone, Bot, FileCode, ChevronRight, CheckCircle, Play, Download } from 'lucide-react';
import { toast } from '@/stores/toast';
import { getAuthHeader } from '@/stores/auth';
import { API_BASE } from '@/lib/api/client';
import { JARVISPageShell, JARVISStagger, JARVISStaggerItem } from '@/components/ui/JARVISPageShell';

const PROJECT_TYPES = [
  { id: 'landing', label: 'Landing Page', icon: Layout, description: 'Marketing page with hero, features, CTA' },
  { id: 'dashboard', label: 'Dashboard', icon: Database, description: 'Admin panel with charts and tables' },
  { id: 'api', label: 'REST API', icon: Server, description: 'Backend API with endpoints and auth' },
  { id: 'fullstack', label: 'Full-Stack App', icon: Globe, description: 'Complete web application' },
  { id: 'mobile', label: 'Mobile App', icon: Smartphone, description: 'React Native mobile application' },
  { id: 'automation', label: 'Automation Script', icon: Bot, description: 'Script to automate a workflow' },
  { id: 'cli', label: 'CLI Tool', icon: FileCode, description: 'Command-line utility' },
  { id: 'component', label: 'UI Component', icon: Code2, description: 'Reusable React/Vue component' },
] as const;

const FRAMEWORKS = {
  landing: ['React + Vite', 'Next.js', 'Astro', 'HTML/CSS'],
  dashboard: ['React + Tailwind', 'Next.js', 'Vue 3', 'Angular'],
  api: ['FastAPI', 'Express.js', 'Go Gin', 'Django REST'],
  fullstack: ['Next.js', 'Remix', 'SvelteKit', 'T3 Stack'],
  mobile: ['React Native', 'Expo', 'Flutter'],
  automation: ['Python', 'Node.js', 'Go', 'Bash'],
  cli: ['Python Click', 'Node.js', 'Go Cobra'],
  component: ['React', 'Vue 3', 'Svelte', 'Web Components'],
} as const;

export default function BuilderPage() {
  const navigate = useNavigate();
  const [step, setStep] = useState<'type' | 'config' | 'building' | 'done'>('type');
  const [projectType, setProjectType] = useState<string>('landing');
  const [framework, setFramework] = useState<string>('React + Vite');
  const [description, setDescription] = useState('');
  const [features, setFeatures] = useState<string[]>(['Authentication', 'Responsive design']);
  const [featureInput, setFeatureInput] = useState('');
  const [result, setResult] = useState<any>(null);
  const [buildProgress, setBuildProgress] = useState<string[]>([]);

  const buildMutation = useMutation({
    mutationFn: async () => {
      const payload = { description, project_type: projectType, framework, features };
      setBuildProgress(['Analyzing requirements…']);

      // Try streaming build
      const response = await fetch(
        `${API_BASE}/builder/projects`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
          body: JSON.stringify(payload),
        }
      );

      if (!response.ok) throw new Error(`Build failed: ${response.statusText}`);

      // Handle both streaming and non-streaming
      const contentType = response.headers.get('content-type') ?? '';
      if (contentType.includes('text/event-stream') && response.body) {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let fullResult: any = null;
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          const chunk = decoder.decode(value);
          chunk.split('\n').forEach(line => {
            if (line.startsWith('data: ')) {
              try {
                const data = JSON.parse(line.slice(6));
                if (data.step) setBuildProgress(prev => [...prev, data.step]);
                if (data.result) fullResult = data.result;
              } catch {}
            }
          });
        }
        return fullResult;
      }

      return response.json();
    },
    onSuccess: (data) => {
      setResult(data);
      setStep('done');
      setBuildProgress(prev => [...prev, '✅ Build complete!']);
    },
    onError: (e) => {
      toast({ kind: 'error', message: `Build failed: ${String(e)}` });
      setStep('config');
    },
  });

  const handleBuild = () => {
    if (!description.trim()) {
      toast({ kind: 'error', message: 'Please describe your project first' });
      return;
    }
    setStep('building');
    buildMutation.mutate();
  };

  const addFeature = () => {
    if (featureInput.trim() && !features.includes(featureInput.trim())) {
      setFeatures(prev => [...prev, featureInput.trim()]);
      setFeatureInput('');
    }
  };

  const frameworks = FRAMEWORKS[projectType as keyof typeof FRAMEWORKS] ?? ['React'];

  return (
    <JARVISPageShell>

      {/* a11y: live region for async updates */}
      <div aria-live="polite" aria-atomic="true" className="sr-only" />
    <JARVISStagger className="max-w-4xl space-y-6">
      {/* Header */}
      <JARVISStaggerItem>
      <div className="flex items-center gap-3">
        <div className="p-2.5 bg-primary/10 rounded-xl">
          <Code2 className="h-6 w-6 text-[#00D4FF]" />
        </div>
        <div>
          <h1 className="text-2xl font-bold">AI Project Builder</h1>
          <p className="text-sm text-muted-foreground mt-0.5">Describe your project and let AI generate the scaffolding</p>
        </div>
      </div>
      </JARVISStaggerItem>

      {/* Progress steps */}
      <JARVISStaggerItem>
      <div className="flex items-center gap-2">
        {(['type', 'config', 'building', 'done'] as const).map((s, i) => (
          <div key={s} className="flex items-center gap-2">
            <div className={`h-7 w-7 rounded-full flex items-center justify-center text-xs font-medium transition-colors ${
              step === s ? 'bg-primary text-primary-foreground' :
              ['type','config','building','done'].indexOf(step) > i ? 'bg-green-500 text-white' :
              'bg-muted text-muted-foreground'
            }`}>
              {['type','config','building','done'].indexOf(step) > i ? <CheckCircle className="h-4 w-4" /> : i + 1}
            </div>
            <span className="text-xs text-muted-foreground capitalize hidden sm:block">{s === 'type' ? 'Project Type' : s === 'config' ? 'Configuration' : s === 'building' ? 'Building' : 'Done'}</span>
            {i < 3 && <ChevronRight className="h-4 w-4 text-muted-foreground/50" />}
          </div>
        ))}
      </div>
      </JARVISStaggerItem>

      {/* Step 1: Project Type */}
      {step === 'type' && (
        <div className="space-y-4">
          <h2 className="text-base font-semibold">What are you building?</h2>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {PROJECT_TYPES.map(pt => (
              <button
                key={pt.id}
                onClick={() => { setProjectType(pt.id); setFramework(FRAMEWORKS[pt.id as keyof typeof FRAMEWORKS]?.[0] ?? 'React'); }}
                className={`p-4 border-2 rounded-xl text-left transition-[color,background-color,border-color,opacity,box-shadow,transform] ${
                  projectType === pt.id
                    ? 'border-primary bg-primary/5'
                    : 'border-border hover:border-[#00D4FF]/40'
                }`}
              >
                <pt.icon className={`h-6 w-6 mb-2 ${projectType === pt.id ? 'text-primary' : 'text-muted-foreground'}`} />
                <p className="text-sm font-medium">{pt.label}</p>
                <p className="text-[10px] text-muted-foreground mt-0.5 leading-tight">{pt.description}</p>
              </button>
            ))}
          </div>
          <button
            onClick={() => setStep('config')}
            className="flex items-center gap-2 px-5 py-2.5 bg-primary text-primary-foreground font-medium rounded-xl hover:opacity-90"
          >
            Continue <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      )}

      {/* Step 2: Configuration */}
      {step === 'config' && (
        <div className="space-y-5">
          <div className="flex items-center gap-3">
            <button onClick={() => setStep('type')} className="text-sm text-muted-foreground hover:text-foreground">← Back</button>
            <h2 className="text-base font-semibold">Configure your project</h2>
          </div>

          {/* Framework */}
          <div>
            <label className="block text-sm font-medium mb-2">Framework / Stack</label>
            <div className="flex flex-wrap gap-2">
              {frameworks.map(fw => (
                <button key={fw} onClick={() => setFramework(fw)}
                  className={`px-3 py-1.5 text-xs rounded-lg border transition-colors ${framework === fw ? 'bg-primary text-primary-foreground border-primary' : 'border-input hover:bg-muted/50'}`}>
                  {fw}
                </button>
              ))}
            </div>
          </div>

          {/* Description */}
          <div>
            <label className="block text-sm font-medium mb-2">Project Description *</label>
            <textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              rows={4}
              placeholder={`Describe your ${PROJECT_TYPES.find(p => p.id === projectType)?.label.toLowerCase()}... e.g. "A SaaS dashboard for tracking API usage with charts, user management, and billing history"`}
              className="w-full px-3 py-2 text-sm border border-input rounded-xl bg-background focus:outline-none focus:ring-2 focus:ring-primary resize-none"
            />
            <p className="text-xs text-muted-foreground mt-1">{description.length}/500 chars — be specific for better output</p>
          </div>

          {/* Features */}
          <div>
            <label className="block text-sm font-medium mb-2">Key Features</label>
            <div className="flex flex-wrap gap-2 mb-2">
              {features.map((f, i) => (
                <span key={i} className="flex items-center gap-1 text-xs bg-primary/10 text-primary px-2.5 py-1 rounded-full">
                  {f}
                  <button onClick={() => setFeatures(prev => prev.filter((_, j) => j !== i))} className="hover:text-red-500">×</button>
                </span>
              ))}
            </div>
            <div className="flex gap-2">
              <input
                value={featureInput}
                onChange={e => setFeatureInput(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && addFeature()}
                placeholder="Add a feature... (press Enter)"
                className="flex-1 px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary"
              />
              <button onClick={addFeature} className="px-3 py-2 text-sm bg-muted rounded-lg hover:bg-muted/80">+</button>
            </div>
          </div>

          <button
            onClick={handleBuild}
            disabled={!description.trim()}
            className="flex items-center gap-2 px-5 py-2.5 bg-primary text-primary-foreground font-medium rounded-xl hover:opacity-90 disabled:opacity-50"
          >
            <Zap className="h-4 w-4" /> Build Project
          </button>
        </div>
      )}

      {/* Step 3: Building */}
      {step === 'building' && (
        <div className="space-y-4">
          <h2 className="text-base font-semibold">Building your project…</h2>
          <div className="bg-card border border-border rounded-xl p-5 space-y-2">
            {buildProgress.map((p, i) => (
              <div key={i} className="flex items-center gap-3 text-sm">
                {i === buildProgress.length - 1 && buildMutation.isPending
                  ? <Loader2 className="h-4 w-4 animate-spin text-primary shrink-0" />
                  : <CheckCircle className="h-4 w-4 text-green-500 shrink-0" />}
                <span>{p}</span>
              </div>
            ))}
            {buildMutation.isPending && (
              <div className="flex items-center gap-3 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin shrink-0" />
                <span>AI is generating your project…</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Step 4: Done */}
      {step === 'done' && result && (
        <div className="space-y-5">
          <div className="flex items-center gap-3">
            <CheckCircle className="h-7 w-7 text-green-500" />
            <h2 className="text-base font-semibold">Project generated!</h2>
          </div>

          {/* Result card */}
          <div className="bg-card border border-border rounded-xl overflow-hidden">
            <div className="px-5 py-4 border-b border-border flex items-center justify-between">
              <div>
                <p className="font-medium">{result.name ?? description.slice(0, 50)}</p>
                <p className="text-xs text-muted-foreground mt-0.5">{framework} · {PROJECT_TYPES.find(p => p.id === projectType)?.label}</p>
              </div>
              <div className="flex gap-2">
                {result.goal_id && (
                  <button
                    onClick={() => navigate(`/goals/${result.goal_id}`)}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded-lg hover:opacity-90"
                  >
                    <Play className="h-3 w-3" /> View Execution
                  </button>
                )}
                {result.download_url && (
                  <a href={result.download_url} download
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-input rounded-lg hover:bg-muted/50">
                    <Download className="h-3 w-3" /> Download
                  </a>
                )}
              </div>
            </div>

            {/* Files generated */}
            {result.files && result.files.length > 0 && (
              <div className="p-4 space-y-1.5">
                <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Generated Files</p>
                {result.files.map((f: any, i: number) => (
                  <div key={i} className="flex items-center gap-2 py-1.5 border-b border-border/50 last:border-0">
                    <FileCode className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                    <span className="text-xs font-mono truncate">{f.path ?? f.name ?? f}</span>
                    {f.size && <span className="text-[10px] text-muted-foreground ml-auto shrink-0">{f.size}</span>}
                  </div>
                ))}
              </div>
            )}

            {/* Summary */}
            {result.summary && (
              <div className="px-4 pb-4">
                <p className="text-xs text-muted-foreground leading-relaxed">{result.summary}</p>
              </div>
            )}
          </div>

          <div className="flex gap-3">
            <button
              onClick={() => { setStep('type'); setResult(null); setBuildProgress([]); setDescription(''); }}
              className="px-4 py-2 text-sm border border-input rounded-xl hover:bg-muted/50"
            >
              Build Another
            </button>
            {result.goal_id && (
              <button
                onClick={() => navigate(`/goals/${result.goal_id}`)}
                className="px-4 py-2 text-sm bg-primary text-primary-foreground rounded-xl hover:opacity-90"
              >
                View in Goals →
              </button>
            )}
          </div>
        </div>
      )}
    </JARVISStagger>
    </JARVISPageShell>
  );
}
