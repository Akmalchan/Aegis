import { useEffect, useState } from "react"
import { AnimatePresence, motion } from "motion/react"
import { slides } from "./slides"
import "./deck.css"

function useScale() {
  const [s, setS] = useState(1)
  useEffect(() => {
    const f = () => setS(Math.min(window.innerWidth / 1920, window.innerHeight / 1080))
    f(); window.addEventListener("resize", f); return () => window.removeEventListener("resize", f)
  }, [])
  return s
}

export default function App() {
  const init = Math.max(0, Math.min(slides.length - 1, (parseInt(location.hash.slice(1)) || 1) - 1))
  const [i, setI] = useState(init)
  const [notes, setNotes] = useState(false)
  const scale = useScale()

  useEffect(() => { location.hash = String(i + 1) }, [i])
  useEffect(() => {
    const go = (d: number) => setI((x) => Math.max(0, Math.min(slides.length - 1, x + d)))
    const k = (e: KeyboardEvent) => {
      if (["ArrowRight", " ", "PageDown"].includes(e.key)) { e.preventDefault(); go(1) }
      else if (["ArrowLeft", "PageUp"].includes(e.key)) { e.preventDefault(); go(-1) }
      else if (e.key === "Home") setI(0)
      else if (e.key === "End") setI(slides.length - 1)
      else if (e.key.toLowerCase() === "n") setNotes((v) => !v)
    }
    window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k)
  }, [])

  const still = new URLSearchParams(location.search).has("still")

  return (
    <>
      <div className="stage" onClick={() => setI((x) => Math.min(slides.length - 1, x + 1))}>
        <div style={{ width: 1920 * scale, height: 1080 * scale, position: "relative" }}>
          <AnimatePresence mode="wait" initial={false}>
            <motion.div
              key={i}
              initial={still ? false : { opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={still ? undefined : { opacity: 0 }}
              transition={{ duration: 0.25 }}
              style={{ position: "absolute", inset: 0, transformOrigin: "top left", transform: `scale(${scale})`, width: 1920, height: 1080 }}
            >
              {slides[i].el}
            </motion.div>
          </AnimatePresence>
        </div>
      </div>
      <div className="counter">{i + 1} / {slides.length}</div>
      {notes && <aside className="notes" onClick={(e) => e.stopPropagation()}>{slides[i].notes}</aside>}
    </>
  )
}
