// The small in-house component set the redesign shell needs (build document §10: no heavy UI framework).
// Every colour, radius and type size comes from the generated tokens (src/styles/tokens.css).
import { useEffect, type ButtonHTMLAttributes, type ReactNode } from "react";
import type { Tone } from "../lib/view";

const ICONS = {
  plus: "M12 5v14M5 12h14",
  refresh: "M20 11a8 8 0 0 0-14.9-3.5M4 4v4h4M4 13a8 8 0 0 0 14.9 3.5M20 20v-4h-4",
  close: "M6 6l12 12M18 6L6 18",
  warning: "M12 3.5l9 16.5H3l9-16.5zM12 10v4.5M12 17.5v.01",
  check: "M5 12.5l4.5 4.5L19 7.5",
  checkCircle: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM8 12.5l3 3 5-6",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2",
  xCircle: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM9 9l6 6M15 9l-6 6",
  upload: "M12 16V4M7 9l5-5 5 5M4 20h16",
  copy: "M9 9h11v11H9zM5 15H4V4h11v1",
  arrowRight: "M5 12h14M13 6l6 6-6 6",
  scan: "M4 8V4h4M20 8V4h-4M4 16v4h4M20 16v4h-4M7 12h10",
  download: "M12 4v12M7 11l5 5 5-5M4 20h16",
  server: "M4 4h16v6H4zM4 14h16v6H4zM8 7h.01M8 17h.01",
  layers: "M12 3l9 5-9 5-9-5 9-5zM3 13l9 5 9-5",
  spark: "M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5L18 18M6 18l2.5-2.5M15.5 8.5L18 6",
  graph: "M5 7a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM19 7a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM12 21a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM6.2 6.7l4.8 10M17.8 6.7L13 16.7",
  bot: "M5 8h14v11H5zM12 8V4M9 13h.01M15 13h.01M9 16.5h6",
  flow: "M4 6h5v4H4zM15 14h5v4h-5zM9 8h3v8h3",
  chat: "M4 5h16v11H9l-5 4z",
  shield: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3z",
  flask: "M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4a2 2 0 0 0 1.8-3l-5-9V3",
  plug: "M9 2v5M15 2v5M6 7h12v4a6 6 0 0 1-12 0zM12 17v5",
  message: "M4 6h16v12H4zM4 7l8 6 8-6",
  lock: "M6 11h12v10H6zM8 11V7a4 4 0 0 1 8 0v4",
  file: "M6 3h8l4 4v14H6zM14 3v4h4M9 13h6M9 17h6",
  folder: "M3 6h6l2 2h10v11H3z",
  gear: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1",
  logo: "M12 4a2 2 0 1 0 0 .01M5 18a2 2 0 1 0 0 .01M19 18a2 2 0 1 0 0 .01M12 6v5M12 11l-6 5.5M12 11l6 5.5",
} as const;

export type IconName = keyof typeof ICONS;

export function Icon({ name, size = 16, className = "" }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}
      strokeLinecap="round" strokeLinejoin="round" className={`shrink-0 ${className}`} aria-hidden="true">
      <path d={ICONS[name]} />
    </svg>
  );
}

export const TONE_CLASS: Record<Tone, string> = {
  success: "bg-success-tint text-success",
  warning: "bg-warning-tint text-warning",
  error: "bg-error-tint text-on-error-tint",
  neutral: "bg-container-high text-text-secondary",
  info: "bg-primary-tint text-primary",
};

export function Chip({ tone, children, className = "" }: { tone: Tone; children: ReactNode; className?: string }) {
  return (
    <span className={`inline-flex items-center h-6 px-2.5 rounded-chip text-small font-semibold whitespace-nowrap ${TONE_CLASS[tone]} ${className}`}>
      {children}
    </span>
  );
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger"; icon?: IconName };

export function Button({ variant = "secondary", icon, className = "", children, ...rest }: ButtonProps) {
  const look =
    variant === "primary"
      ? "bg-primary text-on-primary border-primary hover:bg-primary-hover"
      : variant === "danger"
        ? "bg-white text-error border-error hover:bg-error-tint"
        : "bg-white text-text border-border hover:bg-container-low";
  return (
    <button type="button" {...rest}
      className={`inline-flex items-center justify-center gap-2 h-control px-3.5 rounded-control border text-[13px] font-semibold cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed ${look} ${className}`}>
      {icon && <Icon name={icon} />}
      {children}
    </button>
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={`bg-white border border-hairline rounded-card ${className}`}>{children}</section>;
}

export function Label({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`text-label uppercase text-text-secondary ${className}`}>{children}</div>;
}

export function Mono({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <span className={`font-mono text-[12px] ${className}`}>{children}</span>;
}

export function PageHeader({ crumb, title, subtitle, actions }: { crumb: ReactNode; title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="flex items-start justify-between gap-6 mb-6">
      <div className="min-w-0">
        <div className="text-small text-text-secondary mb-1">{crumb}</div>
        <h1 className="text-title m-0">{title}</h1>
        {subtitle && <p className="text-body text-text-secondary mt-1.5 mb-0 max-w-[720px]">{subtitle}</p>}
      </div>
      {actions && <div className="flex gap-2 shrink-0">{actions}</div>}
    </header>
  );
}

export function KpiTile({ label, value, unit, note, tone = "neutral" }: { label: string; value: ReactNode; unit?: string; note?: ReactNode; tone?: Tone }) {
  const noteColor = tone === "success" ? "text-success" : tone === "warning" ? "text-warning" : tone === "error" ? "text-error" : "text-text-secondary";
  return (
    <Card className="p-5 min-w-0">
      <Label>{label}</Label>
      <div className="mt-2 flex items-baseline gap-2">
        <span className="text-[28px] leading-8 font-bold num">{value}</span>
        {unit && <span className="text-body text-text-secondary">{unit}</span>}
      </div>
      {note && <div className={`mt-2 text-small ${noteColor}`}>{note}</div>}
    </Card>
  );
}

export function Banner({ tone, children, className = "" }: { tone: Tone; children: ReactNode; className?: string }) {
  const icon: IconName = tone === "error" ? "xCircle" : tone === "warning" ? "warning" : tone === "success" ? "checkCircle" : "clock";
  return (
    <div role={tone === "error" ? "alert" : "status"} className={`flex gap-2.5 items-start rounded-control px-3.5 py-2.5 text-body ${TONE_CLASS[tone]} ${className}`}>
      <Icon name={icon} className="mt-0.5" />
      <div className="min-w-0">{children}</div>
    </div>
  );
}

export function Spinner() {
  return <span className="inline-block size-3.5 rounded-full border-2 border-current border-r-transparent animate-spin" aria-hidden="true" />;
}

function useEscape(onClose: () => void) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
}

const SCRIM = "fixed inset-0 z-40 bg-[rgba(15,23,42,0.25)] backdrop-blur-[2px]";
const POPOVER_SHADOW = "shadow-[0_1px_2px_0_rgba(15,23,42,0.05),0_4px_12px_0_rgba(15,23,42,0.08)]";

export function Modal({ title, subtitle, icon, onClose, children, footer, width = 640 }: {
  title: ReactNode; subtitle?: ReactNode; icon?: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; width?: number;
}) {
  useEscape(onClose);
  return (
    <div className={`${SCRIM} flex items-start justify-center overflow-y-auto px-4 py-16`} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal="true" className={`w-full bg-white border border-hairline rounded-card ${POPOVER_SHADOW}`} style={{ maxWidth: width }}>
        <header className="flex items-start gap-3 px-6 pt-5 pb-4 border-b border-hairline">
          {icon}
          <div className="flex-1 min-w-0">
            <h2 className="text-section m-0">{title}</h2>
            {subtitle && <p className="text-small text-text-secondary mt-0.5 mb-0">{subtitle}</p>}
          </div>
          <button type="button" aria-label="Close" onClick={onClose} className="p-1 rounded-control text-text-secondary hover:bg-container-low cursor-pointer">
            <Icon name="close" />
          </button>
        </header>
        <div className="px-6 py-5">{children}</div>
        {footer && <footer className="flex justify-end gap-2 px-6 py-4 border-t border-hairline">{footer}</footer>}
      </div>
    </div>
  );
}

export function Drawer({ title, onClose, children, footer }: { title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode }) {
  useEscape(onClose);
  return (
    <div className={SCRIM} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside role="dialog" aria-modal="true" className={`absolute right-0 top-0 h-full w-[440px] max-w-full bg-white border-l border-hairline flex flex-col ${POPOVER_SHADOW}`}>
        <header className="flex items-center justify-between px-6 h-16 border-b border-hairline shrink-0">
          <h2 className="text-section m-0">{title}</h2>
          <button type="button" aria-label="Close" onClick={onClose} className="p-1 rounded-control text-text-secondary hover:bg-container-low cursor-pointer">
            <Icon name="close" />
          </button>
        </header>
        <div className="flex-1 overflow-y-auto px-6 py-5">{children}</div>
        {footer && <footer className="flex justify-end gap-2 px-6 py-4 border-t border-hairline shrink-0">{footer}</footer>}
      </aside>
    </div>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block mb-4">
      <Label className="mb-1.5">{label}</Label>
      {children}
      {hint && <div className="text-small text-text-secondary mt-1.5">{hint}</div>}
    </label>
  );
}

export const INPUT = "w-full h-control px-3 rounded-control border border-border bg-white text-body outline-none focus:border-primary focus:ring-1 focus:ring-primary";
