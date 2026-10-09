import { useRef, createRef, type RefObject } from "react"
import { AnimatedBeam } from "@/components/ui/animated-beam"

export type Node = { title: string; sub?: string; kind?: "llm" | "pass" | "plain" }

export function Pipeline({ nodes, gap = 44 }: { nodes: Node[]; gap?: number }) {
  const container = useRef<HTMLDivElement>(null)
  const refs = useRef<RefObject<HTMLDivElement | null>[]>(nodes.map(() => createRef<HTMLDivElement>()))
  return (
    <div ref={container} style={{ position: "relative", display: "flex", alignItems: "center", gap }}>
      {nodes.map((n, i) => (
        <div key={i} ref={refs.current[i]} className={`node ${n.kind ?? ""}`}>
          {n.title}
          {n.sub && <small>{n.sub}</small>}
        </div>
      ))}
      {nodes.slice(1).map((_, i) => (
        <AnimatedBeam
          key={i}
          containerRef={container}
          fromRef={refs.current[i]}
          toRef={refs.current[i + 1]}
          curvature={0}
          pathColor="#000"
          pathOpacity={0.18}
          pathWidth={2}
          gradientStartColor="#000"
          gradientStopColor="#000"
          duration={3}
          delay={i * 0.35}
        />
      ))}
    </div>
  )
}
