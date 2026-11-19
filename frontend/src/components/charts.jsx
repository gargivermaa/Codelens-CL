import { Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { RISK } from "./ui.jsx";

// Recessive axes/grid; text uses ink tokens, never series colors.
const AXIS = { stroke: "#26304a", tick: { fill: "#8f98ad", fontSize: 12 }, tickLine: false };
const GRID = { stroke: "#1f2840", vertical: false };
// Categorical slots (validated on the #131a2a surface, all checks pass), fixed order.
export const CATEGORICAL = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"];

function TooltipBox({ active, payload, label, format = (v) => v }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-line bg-raised px-3 py-2 text-xs shadow-lg">
      {label != null && <div className="mb-1 font-medium text-ink">{label}</div>}
      {payload.map((p) => (
        <div key={p.dataKey} className="flex items-center gap-2 text-muted">
          <span className="h-2 w-2 rounded-full" style={{ background: p.color || p.payload.fill }} aria-hidden />
          {p.name}: <span className="tabular-nums text-ink">{format(p.value)}</span>
        </div>
      ))}
    </div>
  );
}

/** Bars per risk label (low → critical). Each bar is labelled on the axis, so color is never the only cue. */
export function RiskDistributionChart({ distribution, height = 220 }) {
  const data = ["low", "medium", "high", "critical"].map((key) => ({
    key, name: RISK[key].label, value: distribution?.[key] || 0, fill: RISK[key].color,
  }));
  return (
    <div style={{ height }} role="img" aria-label={data.map((d) => `${d.name}: ${d.value}`).join(", ")}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, left: -20, bottom: 0 }} barCategoryGap="28%">
          <CartesianGrid {...GRID} />
          <XAxis dataKey="name" {...AXIS} />
          <YAxis allowDecimals={false} {...AXIS} axisLine={false} />
          <Tooltip cursor={{ fill: "#ffffff08" }} content={<TooltipBox />} />
          <Bar dataKey="value" name="Pull requests" radius={[4, 4, 0, 0]} maxBarSize={56}>
            {data.map((d) => <Cell key={d.key} fill={d.fill} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Single-series horizontal bars (magnitude by category). */
export function HorizontalBars({ data, dataKey = "count", nameKey = "category", color = CATEGORICAL[0], height }) {
  const h = height || Math.max(120, data.length * 40 + 20);
  return (
    <div style={{ height: h }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 24, left: 8, bottom: 4 }} barCategoryGap="30%">
          <CartesianGrid stroke="#1f2840" horizontal={false} />
          <XAxis type="number" allowDecimals={false} {...AXIS} />
          <YAxis type="category" dataKey={nameKey} width={110} {...AXIS} axisLine={false} />
          <Tooltip cursor={{ fill: "#ffffff08" }} content={<TooltipBox />} />
          <Bar dataKey={dataKey} name="Findings" fill={color} radius={[0, 4, 4, 0]} maxBarSize={22} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Reviews per day: one series per chart (no dual axis). */
export function HistoryChart({ data, dataKey, name, color = CATEGORICAL[0], height = 220, format }) {
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 12, left: -20, bottom: 0 }}>
          <CartesianGrid {...GRID} />
          <XAxis dataKey="date" {...AXIS} tickFormatter={(d) => d.slice(5)} />
          <YAxis allowDecimals={dataKey === "avg_risk"} {...AXIS} axisLine={false}
            domain={dataKey === "avg_risk" ? [0, 1] : [0, "auto"]} />
          <Tooltip cursor={{ stroke: "#8f98ad", strokeDasharray: "3 3" }} content={<TooltipBox format={format} />} />
          <Line type="monotone" dataKey={dataKey} name={name} stroke={color} strokeWidth={2}
            dot={{ r: 4, strokeWidth: 2, stroke: "#131a2a", fill: color }} activeDot={{ r: 5 }} connectNulls />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Count per day as bars (reads correctly even with a single day of data). */
export function DailyBars({ data, dataKey, name, color = CATEGORICAL[0], height = 220 }) {
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, left: -20, bottom: 0 }} barCategoryGap="30%">
          <CartesianGrid {...GRID} />
          <XAxis dataKey="date" {...AXIS} tickFormatter={(d) => d.slice(5)} />
          <YAxis allowDecimals={false} {...AXIS} axisLine={false} />
          <Tooltip cursor={{ fill: "#ffffff08" }} content={<TooltipBox />} />
          <Bar dataKey={dataKey} name={name} fill={color} radius={[4, 4, 0, 0]} maxBarSize={40} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
