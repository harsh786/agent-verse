type SliderProps = {
  value?: number[];
  onValueChange?: (v: number[]) => void;
  min?: number;
  max?: number;
  step?: number;
  className?: string;
  disabled?: boolean;
};

export function Slider({ value, onValueChange, min = 0, max = 100, step = 1, className = '', disabled }: SliderProps) {
  const v = value?.[0] ?? min;
  return <input type="range" min={min} max={max} step={step} value={v}
    onChange={e => onValueChange?.([Number(e.target.value)])}
    disabled={disabled}
    className={`w-full h-2 appearance-none rounded-full bg-white/[0.10] accent-[#00D4FF] cursor-pointer disabled:opacity-50 ${className}`}
  />;
}
