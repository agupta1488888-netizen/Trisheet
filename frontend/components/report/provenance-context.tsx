"use client";

/**
 * The link between a figure and its reference card.
 *
 * One piece of state — which source is currently active — shared by every
 * figure in the report and every card in the rail. Pointing at a figure raises
 * its card; pointing at a card raises every figure drawn from it. The
 * relationship is symmetric on purpose: the rail is not a legend, it is the
 * other half of the document.
 *
 * The provider owns no data. It is handed a `SourceIndex` and holds a
 * highlight.
 */

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react";

import type { SourceIndex } from "@/lib/provenance";

interface ProvenanceContextValue {
  index: SourceIndex;
  /** Accession number of the raised source, or null. */
  activeSourceId: string | null;
  setActiveSourceId: (sourceId: string | null) => void;
}

const ProvenanceContext = createContext<ProvenanceContextValue | null>(null);

export function ProvenanceProvider({
  index,
  children,
}: {
  index: SourceIndex;
  children: React.ReactNode;
}) {
  const [activeSourceId, setActiveSourceId] = useState<string | null>(null);

  const value = useMemo<ProvenanceContextValue>(
    () => ({ index, activeSourceId, setActiveSourceId }),
    [index, activeSourceId],
  );

  return (
    <ProvenanceContext.Provider value={value}>
      {children}
    </ProvenanceContext.Provider>
  );
}

/**
 * Throws when used outside a provider. That is a programming error, not a
 * runtime condition — a figure with no provenance context has no source, and
 * failing loudly in development is the point.
 */
export function useProvenance(): ProvenanceContextValue {
  const value = useContext(ProvenanceContext);
  if (value === null) {
    throw new Error("useProvenance must be used inside a ProvenanceProvider");
  }
  return value;
}

/** Handlers that raise a source while a figure or card is pointed at. */
export function useSourceHighlight(sourceId: string | null): {
  isActive: boolean;
  handlers: {
    onMouseEnter: () => void;
    onMouseLeave: () => void;
    onFocus: () => void;
    onBlur: () => void;
  };
} {
  const { activeSourceId, setActiveSourceId } = useProvenance();

  const raise = useCallback(() => {
    setActiveSourceId(sourceId);
  }, [setActiveSourceId, sourceId]);

  const clear = useCallback(() => {
    setActiveSourceId(null);
  }, [setActiveSourceId]);

  return {
    isActive: sourceId !== null && sourceId === activeSourceId,
    handlers: {
      onMouseEnter: raise,
      onMouseLeave: clear,
      onFocus: raise,
      onBlur: clear,
    },
  };
}

/** The DOM id of a rail card, so markers can link to it. */
export function sourceAnchorId(sourceId: string): string {
  return `source-${sourceId}`;
}
