// Lightweight inline-SVG sparkline — no charting dependency (keeps the bundle lean and
// avoids the rolldown/native-binding fragility). Renders a small trend line + optional area.

export function Sparkline({
  data,
  width = 90,
  height = 20,
  color = 'currentColor',
  fill = false,
}: {
  data: number[]
  width?: number
  height?: number
  color?: string
  fill?: boolean
}) {
  if (!data || data.length < 2) {
    return <svg width={width} height={height} aria-hidden />
  }
  const max = Math.max(...data)
  const min = Math.min(...data, 0)
  const range = max - min || 1
  const n = data.length
  const pts = data.map((v, i) => {
    const x = (i / (n - 1)) * width
    const y = height - ((v - min) / range) * (height - 2) - 1
    return [x, y] as const
  })
  const line = pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  const area = `0,${height} ` + line + ` ${width},${height}`
  return (
    <svg width={width} height={height} className="overflow-visible" aria-hidden>
      {fill && <polygon points={area} fill={color} opacity={0.12} />}
      <polyline points={line} fill="none" stroke={color} strokeWidth={1.5}
                strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}
