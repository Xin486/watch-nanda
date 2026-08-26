<template>
  <div class="ring-wrapper">
    <div class="chart-container">
      <svg viewBox="0 0 36 36" class="circular-chart" :class="statusColor">
        <path class="circle-bg" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" />
        <path class="circle" :stroke-dasharray="`${percent}, 100`" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" />
      </svg>
      <div class="percentage-text">
        <!-- 提供 centerText 时显示自定义文本（如 GPU 总功率），否则显示百分比 -->
        <span class="num" :class="{ long: isLongText }">{{ displayText }}</span><span class="pct">{{ displayUnit }}</span>
      </div>
    </div>
    <div class="label">{{ label }}</div>
    <div class="sub-label">{{ subText }}</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  percent: { type: Number, default: 0 },
  label: String,
  subText: String,
  // 可选：中间显示的自定义文本与单位（不传则显示 percent + %）
  centerText: { type: [String, Number], default: undefined },
  centerUnit: { type: String, default: '' }
})

const displayText = computed(() => props.centerText !== undefined ? props.centerText : props.percent)
const displayUnit = computed(() => props.centerText !== undefined ? (props.centerUnit || '') : '%')
// 自定义文本较长时缩小字号，避免撑破圆环
const isLongText = computed(() => String(displayText.value).length >= 4)

const statusColor = computed(() => {
  if (props.percent >= 85) return 'danger'
  if (props.percent >= 60) return 'warning'
  return 'normal'
})
</script>

<style scoped>
.ring-wrapper { display: flex; flex-direction: column; align-items: center; width: 72px; }
.chart-container { position: relative; width: 100%; display: flex; align-items: center; justify-content: center; }

.circular-chart { display: block; width: 100%; max-height: 250px; }
.circle-bg { fill: none; stroke: #f1f5f9; stroke-width: 3.5; }
.circle { fill: none; stroke-width: 3.5; stroke-linecap: round; transition: stroke-dasharray 0.5s ease; }

.normal .circle { stroke: #10b981; }
.warning .circle { stroke: #f59e0b; }
.danger .circle { stroke: #ef4444; }

/* 数字排版优化 */
.percentage-text { position: absolute; display: flex; align-items: baseline; color: #1e293b; }
.num { font-size: 17px; font-weight: 700; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
.num.long { font-size: 12px; }
.pct { font-size: 11px; font-weight: 600; margin-left: 1px; color: #64748b; }

.label { margin-top: 6px; font-size: 13px; color: #475569; font-weight: bold; }
.sub-label { font-size: 11px; color: #94a3b8; margin-top: 2px; }
</style>
