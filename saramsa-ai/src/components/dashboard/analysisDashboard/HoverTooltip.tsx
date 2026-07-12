'use client';

import { useState, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

interface HoverTooltipProps {
  /** Tooltip body — string or JSX. */
  content: ReactNode;
  children: ReactNode;
  /** className for the inline trigger wrapper. */
  className?: string;
  /** Extra classes for the floating tooltip (e.g. max-width). */
  tooltipClassName?: string;
}

/**
 * Lightweight styled tooltip that renders via a portal to <body> with fixed
 * positioning. Because it's not a descendant of the hovered element, it is
 * NOT clipped by any `overflow` ancestor (e.g. the scrollable feature list) —
 * which the plain CSS `absolute` tooltip was, and native `title` looks poor.
 */
export function HoverTooltip({ content, children, className, tooltipClassName }: HoverTooltipProps) {
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const ref = useRef<HTMLSpanElement>(null);

  const show = () => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    // Anchor just above the trigger, horizontally centered.
    setPos({ top: r.top - 8, left: r.left + r.width / 2 });
  };
  const hide = () => setPos(null);

  return (
    <span
      ref={ref}
      className={className}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
    >
      {children}
      {pos && content != null && typeof document !== 'undefined' &&
        createPortal(
          <div
            role="tooltip"
            style={{ position: 'fixed', top: pos.top, left: pos.left, transform: 'translate(-50%, -100%)' }}
            className={`z-[9999] pointer-events-none max-w-xs px-3 py-2 rounded-xl shadow-lg
              bg-background/95 border border-border/60 text-foreground text-xs leading-snug
              ${tooltipClassName ?? ''}`}
          >
            {content}
          </div>,
          document.body
        )}
    </span>
  );
}
