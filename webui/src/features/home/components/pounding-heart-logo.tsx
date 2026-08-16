/*
Copyright (C) 2023-2026 QuantumNous — GNU AGPL v3
POUNDING Interactive Heart Logo — SVG-based, eyes track mouse, random blink
Based on PoundingInteractiveLogo.tsx from POUNDING desktop
*/
import { useRef, useState, useEffect, useMemo, type MouseEvent } from 'react'

interface Props {
  size?: number
  compact?: boolean
}

// SVG coordinate system (from original PoundingInteractiveLogo)
const VB_W = 1759
const VB_H = 1765
const EYES_X = 405
const EYES_Y = 334
const EYES_W = 949
const EYES_H = 726
const EYES_CX = EYES_X + EYES_W / 2
const EYES_CY = EYES_Y + EYES_H / 2
const NOSE_X = 1319
const NOSE_Y = 998
const NOSE_SZ = 63

export function PoundingHeartLogo({ size = 34, compact = false }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [offset, setOffset] = useState({ x: 0, y: 0 })
  const [blinking, setBlinking] = useState(false)

  const maxX = compact ? 28 : 56
  const maxY = compact ? 20 : 40

  // Mouse tracking (window-level, matching original behavior)
  useEffect(() => {
    const handleMove = (e: globalThis.PointerEvent | globalThis.MouseEvent) => {
      const rect = containerRef.current?.getBoundingClientRect()
      if (!rect) return
      const px = (e.clientX - rect.left) / rect.width
      const py = (e.clientY - rect.top) / rect.height
      setOffset({ x: Math.max(-1, Math.min(1, (px - 0.5) * 2)) * maxX, y: Math.max(-1, Math.min(1, (py - 0.5) * 2)) * maxY })
    }
    const reset = () => setOffset({ x: 0, y: 0 })
    window.addEventListener('pointermove', handleMove, { passive: true })
    window.addEventListener('mousemove', handleMove, { passive: true })
    window.addEventListener('mouseleave', reset)
    window.addEventListener('blur', reset)
    return () => {
      window.removeEventListener('pointermove', handleMove)
      window.removeEventListener('mousemove', handleMove)
      window.removeEventListener('mouseleave', reset)
      window.removeEventListener('blur', reset)
    }
  }, [maxX, maxY])

  // Random blinking
  useEffect(() => {
    let disposed = false
    let blinkT: ReturnType<typeof setTimeout>
    let resetT: ReturnType<typeof setTimeout>

    const blinkOnce = () => { setBlinking(true); resetT = setTimeout(() => setBlinking(false), 160) }
    const schedule = () => {
      if (disposed) return
      blinkT = setTimeout(() => { blinkOnce(); if (!disposed && Math.random() < 0.18) setTimeout(blinkOnce, 220); schedule() }, 2600 + Math.random() * 3200)
    }
    schedule()
    return () => { disposed = true; clearTimeout(blinkT); clearTimeout(resetT) }
  }, [])

  const eyesTransform = useMemo(() => `translate(${offset.x} ${offset.y})`, [offset.x, offset.y])
  const blinkTransform = useMemo(() => {
    const sy = blinking ? 0.06 : 1
    return `translate(${EYES_CX} ${EYES_CY}) scale(1 ${sy}) translate(${-EYES_CX} ${-EYES_CY})`
  }, [blinking])

  return (
    <div ref={containerRef} style={{ width: size, height: size, display: 'inline-flex', flexShrink: 0, overflow: 'hidden', borderRadius: 8 }}>
      <svg
        viewBox={`0 0 ${VB_W} ${VB_H}`}
        aria-hidden='true'
        style={{ display: 'block', width: size, height: size }}
        preserveAspectRatio='xMidYMid meet'
      >
        {/* POUNDING red heart base */}
        <image href='/landing/pounding-heart.png' x={0} y={0} width={VB_W} height={VB_H} preserveAspectRatio='none' />
        {/* Eyes — tracks mouse */}
        <g transform={eyesTransform}>
          <g transform={blinkTransform}>
            <image href='/landing/pounding-eyes.png' x={EYES_X} y={EYES_Y} width={EYES_W} height={EYES_H} preserveAspectRatio='none' />
          </g>
        </g>
        {/* Nose dot — tiny, precisely positioned */}
        <image href='/landing/pounding-nose.png' x={NOSE_X} y={NOSE_Y} width={NOSE_SZ} height={NOSE_SZ} preserveAspectRatio='none' />
      </svg>
    </div>
  )
}
