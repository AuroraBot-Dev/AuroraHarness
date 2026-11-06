<template>
  <div
    class="canvas-atmosphere"
    aria-hidden="true"
  >
    <div class="ambient-wash" />
    <div class="ambient-orbit" />
    <svg
      class="ambient-ribbon"
      viewBox="0 0 1400 1000"
      preserveAspectRatio="xMidYMid slice"
    >
      <path d="M 760 -140 C 1280 70 650 350 1080 530 S 1490 850 1080 1140" />
      <path d="M 810 -160 C 1340 70 710 350 1130 530 S 1540 850 1130 1160" />
    </svg>
    <div class="ambient-light" />
  </div>
</template>

<style scoped>
.canvas-atmosphere {
  position: absolute;
  inset: 0;
  overflow: hidden;
  pointer-events: none;
}

.ambient-wash {
  position: absolute;
  top: -22%;
  left: -24%;
  width: 70%;
  height: 110%;
  background: radial-gradient(
    ellipse,
    color-mix(in srgb, var(--accent-source) 11%, transparent),
    transparent 68%
  );
}

.ambient-orbit {
  position: absolute;
  top: 5%;
  right: -22%;
  width: min(80vw, 1120px);
  aspect-ratio: 1;
  border: 1px solid color-mix(in srgb, var(--accent-source) 14%, transparent);
  border-radius: 50%;
  background: radial-gradient(
    circle at 39% 34%,
    transparent 47%,
    color-mix(in srgb, var(--accent-source) 9%, transparent) 70%,
    transparent 71%
  );
  box-shadow: inset 28px 12px 80px color-mix(in srgb, var(--accent-source) 5%, transparent);
  transform: rotate(-28deg) scaleY(0.66);
}

.ambient-orbit::after {
  position: absolute;
  inset: 5%;
  border: 1px solid color-mix(in srgb, var(--accent-source) 9%, transparent);
  border-radius: inherit;
  content: '';
}

.ambient-light {
  position: absolute;
  top: 44%;
  right: 10%;
  width: 46%;
  height: 42%;
  border-radius: 50%;
  background: radial-gradient(
    ellipse,
    color-mix(in srgb, var(--accent-source) 18%, transparent),
    transparent 69%
  );
  filter: blur(44px);
}

.ambient-ribbon {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  overflow: visible;
  fill: none;
  stroke: color-mix(in srgb, var(--accent-source) 12%, transparent);
  stroke-width: 0.8;
  mask-image: linear-gradient(transparent, #000 22%, #000 75%, transparent);
}

.ambient-ribbon path:last-child {
  stroke-opacity: 0.5;
}

@media (max-width: 860px) {
  .ambient-orbit {
    top: 15%;
    right: -48%;
    width: 110vw;
  }
}

@media (prefers-reduced-transparency: reduce) {
  .ambient-wash,
  .ambient-light {
    opacity: 0.35;
  }
}
</style>
