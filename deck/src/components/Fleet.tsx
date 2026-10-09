import { useRef, createRef, type RefObject } from "react"
import { AnimatedBeam } from "@/components/ui/animated-beam"

const SENT = ["sentinel-01", "sentinel-02", "sentinel-03"]
const SUB = ["triage", "remediator", "verifier"]

export function Fleet() {
  const box = useRef<HTMLDivElement>(null)
  const s = useRef<RefObject<HTMLDivElement | null>[]>(SENT.map(() => createRef<HTMLDivElement>()))
  const t = useRef<RefObject<HTMLDivElement | null>[]>(SUB.map(() => createRef<HTMLDivElement>()))
  const hook = useRef<HTMLDivElement>(null)
  return (
    <div ref={box} style={{ position: "relative", display: "grid", gridTemplateColumns: "260px 300px 260px", justifyContent: "space-between", alignItems: "center", rowGap: 22, width: 1100 }}>
      <div ref={hook} className="node" style={{ gridRow: "1 / span 3", width: 240 }}>GitHub webhook<small>push · pull_request</small></div>
      {SENT.map((n, i) => (
        <div key={n} ref={s.current[i]} className="node" style={{ gridColumn: 2, gridRow: i + 1, width: 280 }}>{n}<small>3 repos</small></div>
      ))}
      {SUB.map((n, i) => (
        <div key={n} ref={t.current[i]} className={`node ${n === "verifier" ? "pass" : ""} ${n === "remediator" ? "llm" : ""}`} style={{ gridColumn: 3, gridRow: i + 1, width: 240 }}>{n}<small>sub-agent</small></div>
      ))}
      {SENT.map((_, i) => (
        <AnimatedBeam key={"h" + i} containerRef={box} fromRef={hook} toRef={s.current[i]} curvature={0} pathColor="#000" pathOpacity={0.18} pathWidth={2} gradientStartColor="#000" gradientStopColor="#000" duration={3} delay={i * 0.3} />
      ))}
      {SUB.map((_, i) => (
        <AnimatedBeam key={"s" + i} containerRef={box} fromRef={s.current[1]} toRef={t.current[i]} curvature={0} pathColor="#000" pathOpacity={0.18} pathWidth={2} gradientStartColor="#000" gradientStopColor="#000" duration={3} delay={1 + i * 0.3} />
      ))}
    </div>
  )
}
