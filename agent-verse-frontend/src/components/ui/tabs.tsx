import { useState, createContext, useContext, type ReactNode } from 'react';

const Ctx = createContext<{ active: string; set: (v: string) => void }>({ active: '', set: () => {} });

interface TabsProps { defaultValue?: string; value?: string; onValueChange?: (v: string) => void; children: ReactNode; className?: string; }
export function Tabs({ defaultValue = '', value, onValueChange, children, className = '' }: TabsProps) {
  const [local, setLocal] = useState(defaultValue);
  const active = value ?? local;
  const set = (v: string) => { setLocal(v); onValueChange?.(v); };
  return <Ctx.Provider value={{ active, set }}><div className={className}>{children}</div></Ctx.Provider>;
}

export function TabsList({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`flex gap-1 p-1 bg-white/[0.06] rounded-xl ${className}`}>{children}</div>;
}

export function TabsTrigger({ value, children, className = '' }: { value: string; children: ReactNode; className?: string }) {
  const { active, set } = useContext(Ctx);
  return <button onClick={() => set(value)} aria-selected={active === value} className={`px-4 py-1.5 text-sm rounded-lg font-medium transition-colors ${active === value ? 'bg-[#00D4FF]/15 text-[#00D4FF]' : 'text-[#5A7494] hover:text-[#A0B4CC]'} ${className}`}>{children}</button>;
}

export function TabsContent({ value, children, className = '' }: { value: string; children: ReactNode; className?: string }) {
  const { active } = useContext(Ctx);
  if (active !== value) return null;
  return <div className={className}>{children}</div>;
}
